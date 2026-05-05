"""
quiz_pipeline.py

Generator til quiz-forslag fra PDF:
- læser tekst fra PDF
- bruger smart OCR på sider med lidt tekst
- udtrækker facts fra materialet
- genererer quizspørgsmål ud fra facts
- validerer JSON og kvalitet
- review-step forbedrer spørgsmålene
- regenererer enkeltspørgsmål ud fra facts
"""

import json
import random
import re
import subprocess
import time
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple

import pdfplumber
import requests

from app.services.prompt_builder import (
    build_direct_question_generation_prompt,
    build_direct_repair_prompt,
    build_direct_supplement_prompt,
    build_fact_extraction_prompt,
    build_fact_repair_prompt,
    build_guided_single_supplement_prompt,
    build_question_generation_prompt,
    build_review_prompt,
    build_single_regenerate_prompt,
)
from app.services.prompt_examples import get_examples_for_page_range, get_keywords_for_page_range
from app.services.quiz_validation import (
    filter_out_similar_questions,
    norm_ws,
    question_signature,
    validate_answer_sources,
    validate_against_example_answers,
    validate_age_group_content,
    validate_danish_language_quality,
    validate_mcq_quality,
    validate_option_set_diversity,
    validate_quiz_schema,
    validate_source_pages,
)

try:
    import pytesseract
    from pdf2image import convert_from_bytes
    OCR_AVAILABLE = True
except Exception:
    OCR_AVAILABLE = False


OLLAMA_HOST = "http://localhost:11434"
OLLAMA_CHAT_URL = f"{OLLAMA_HOST}/api/chat"
OLLAMA_GENERATE_URL = f"{OLLAMA_HOST}/api/generate"
OLLAMA_TAGS_URL = f"{OLLAMA_HOST}/api/tags"


@dataclass
class OCRParams:
    use_ocr: bool = True
    max_pages: int = 6
    dpi: int = 130
    lang: str = "dan"


@dataclass
class QuizGenParams:
    model: str = "qwen2.5:7b-instruct"
    num_questions: int = 10
    age_group: str = "B"
    page_from: Optional[int] = None
    page_to: Optional[int] = None

    max_pages: int = 120
    max_chars: int = 5000

    temperature: float = 0.0
    timeout: int = 300
    num_ctx: int = 3072
    num_predict: int = 800
    run_final_review: bool = False
    use_fact_pipeline: bool = False

    ocr: OCRParams = field(default_factory=OCRParams)


@dataclass
class QuizGenDebug:
    extracted_chars: int
    used_ocr: bool
    material_preview: str
    model_latency_s: float
    did_repair: bool
    raw_model_output: str
    page_range: str
    facts_count: int = 0
    facts_preview: str = ""


def extract_text_pdfplumber(
    pdf_bytes: bytes,
    page_from: Optional[int] = None,
    page_to: Optional[int] = None,
    max_pages: int = 120,
) -> str:
    out: List[str] = []

    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        total_pages = len(pdf.pages)
        start = 1 if page_from is None else max(1, page_from)
        end = total_pages if page_to is None else min(total_pages, page_to)
        end = min(end, max_pages)

        if start > end:
            return ""

        for i in range(start, end + 1):
            page = pdf.pages[i - 1]
            text = (page.extract_text() or "").strip()
            if text:
                out.append(f"[Side {i}]\n{text}")

    return "\n\n".join(out)


def extract_text_ocr(
    pdf_bytes: bytes,
    page_from: Optional[int] = None,
    page_to: Optional[int] = None,
    max_pages: int = 12,
    dpi: int = 130,
    lang: str = "dan",
) -> str:
    if not OCR_AVAILABLE:
        return ""

    start = 1 if page_from is None else max(1, page_from)
    end = max_pages if page_to is None else min(page_to, max_pages)

    if start > end:
        return ""

    images = convert_from_bytes(
        pdf_bytes,
        dpi=dpi,
        first_page=start,
        last_page=end,
    )

    parts: List[str] = []
    for idx, img in enumerate(images, start=start):
        txt = (pytesseract.image_to_string(img, lang=lang) or "").strip()
        if txt:
            parts.append(f"[OCR Side {idx}]\n{txt}")

    return "\n\n".join(parts)


def safe_json_loads(s: str) -> Optional[Dict[str, Any]]:
    s = (s or "").strip()
    if not s:
        return None

    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass

    s2 = re.sub(
        r"^```(?:json)?\s*|\s*```$",
        "",
        s,
        flags=re.IGNORECASE | re.MULTILINE,
    ).strip()

    try:
        obj = json.loads(s2)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass

    start = s2.find("{")
    end = s2.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = s2[start:end + 1]
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass

    return None


def salvage_json_array_items(text: str, array_key: str) -> List[Dict[str, Any]]:
    text = text or ""
    key_match = re.search(rf'"{re.escape(array_key)}"\s*:\s*\[', text)
    if not key_match:
        return []

    decoder = json.JSONDecoder()
    idx = key_match.end()
    items: List[Dict[str, Any]] = []

    while idx < len(text):
        while idx < len(text) and text[idx] in " \r\n\t,":
            idx += 1

        if idx >= len(text) or text[idx] == "]":
            break

        if text[idx] != "{":
            idx += 1
            continue

        try:
            item, end_idx = decoder.raw_decode(text[idx:])
        except json.JSONDecodeError:
            break

        if isinstance(item, dict):
            items.append(item)

        idx += end_idx

    return items


def quiz_obj_from_output(text: str) -> Optional[Dict[str, Any]]:
    obj = safe_json_loads(text)
    if obj:
        return obj

    questions = salvage_json_array_items(text, "questions")
    if questions:
        return {
            "quiz_title": "Spørgsmålsforslag",
            "questions": questions,
        }

    return None


def candidate_count(requested: int) -> int:
    return min(max(requested + 1, requested), 20)


def ensure_enough_questions(obj: Dict[str, Any], minimum_count: int) -> None:
    questions = obj.get("questions", []) if isinstance(obj, dict) else []
    if not isinstance(questions, list) or len(questions) < minimum_count:
        raise RuntimeError(
            f"Der blev kun lavet {len(questions)} brugbare spørgsmål, "
            f"men der skal bruges mindst {minimum_count}. "
            "Prøv færre endelige spørgsmål eller vælg flere sider."
        )


def normalize_facts_obj(obj: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(obj, dict):
        return {"facts": []}

    facts = obj.get("facts")
    if not isinstance(facts, list):
        return {"facts": []}

    normalized: List[Dict[str, Any]] = []
    seen = set()

    for i, fact in enumerate(facts, start=1):
        if not isinstance(fact, dict):
            continue

        raw_fact = str(fact.get("fact", "")).strip()
        if not raw_fact:
            continue

        correct_answer = str(fact.get("correct_answer", "")).strip()
        if not correct_answer:
            correct_answer = raw_fact

        topic = str(fact.get("topic", "")).strip() or "generelt"
        list_group = str(fact.get("list_group", "")).strip()

        source_page = fact.get("source_page", 0)
        try:
            source_page = int(source_page)
        except Exception:
            source_page = 0

        importance = str(fact.get("importance", "medium")).strip().lower()
        if importance not in {"low", "medium", "high"}:
            importance = "medium"

        item = {
            "fact_id": i,
            "topic": topic,
            "list_group": list_group,
            "source_page": source_page,
            "fact": raw_fact,
            "correct_answer": correct_answer,
            "importance": importance,
        }

        key = (topic.lower(), norm_ws(raw_fact).lower())
        if key in seen:
            continue

        seen.add(key)
        normalized.append(item)

    return {"facts": normalized}


def normalize_quiz_obj(obj: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(obj, dict):
        return obj

    if "quiz_title" not in obj or not str(obj.get("quiz_title", "")).strip():
        obj["quiz_title"] = "Spørgsmålsforslag"

    questions = obj.get("questions")
    if not isinstance(questions, list):
        obj["questions"] = []
        return obj

    normalized_questions = []

    for i, q in enumerate(questions, start=1):
        if not isinstance(q, dict):
            continue

        item: Dict[str, Any] = {
            "id": q.get("id", i),
            "type": "mcq",
            "question": str(q.get("question", "")).strip(),
            "explanation": str(q.get("explanation", "")).strip(),
            "topic": str(q.get("topic", "")).strip() or "generelt",
            "list_group": str(q.get("list_group", "")).strip(),
            "difficulty": str(q.get("difficulty", "")).strip().lower() or "medium",
            "source_fact_id": q.get("source_fact_id", i),
            "source_page": q.get("source_page", 0),
            "origin": str(q.get("origin", "generated")).strip().lower() or "generated",
        }

        if not item["explanation"]:
            item["explanation"] = "Forklaring mangler."

        if item["difficulty"] not in {"easy", "medium", "hard"}:
            item["difficulty"] = "medium"

        if item["origin"] not in {"example", "generated"}:
            item["origin"] = "generated"

        try:
            item["source_fact_id"] = int(item["source_fact_id"])
        except Exception:
            item["source_fact_id"] = i

        try:
            item["source_page"] = int(item["source_page"])
        except Exception:
            item["source_page"] = 0

        def clean_option_label(value: str) -> str:
            return re.sub(r"^\s*[A-Ca-c]\s*[\)\.\:\-]\s*", "", value).strip()

        opts = q.get("options", [])
        if not isinstance(opts, list):
            opts = []

        opts = [clean_option_label(str(x)) for x in opts if str(x).strip()]

        unique_opts: List[str] = []
        seen = set()
        for o in opts:
            key = norm_ws(o).lower()
            if key not in seen:
                unique_opts.append(o)
                seen.add(key)

        try:
            ai = int(q.get("answer_index", 0))
        except Exception:
            ai = 0

        if ai not in [0, 1, 2]:
            ai = 0

        correct_answer = clean_option_label(str(q.get("correct_answer", "")).strip())
        if not correct_answer and unique_opts:
            correct_answer = unique_opts[ai]

        if correct_answer:
            correct_key = norm_ws(correct_answer).lower()
            matching_index = next(
                (idx for idx, opt in enumerate(unique_opts[:3]) if norm_ws(opt).lower() == correct_key),
                None,
            )
            if matching_index is None:
                if unique_opts:
                    unique_opts[min(ai, len(unique_opts) - 1)] = correct_answer
                else:
                    unique_opts.append(correct_answer)
            else:
                ai = matching_index

        item["options"] = unique_opts[:3]
        item["answer_index"] = ai
        item["correct_answer"] = correct_answer
        normalized_questions.append(item)

    obj["questions"] = filter_out_similar_questions(normalized_questions)
    return obj


def shuffle_questions(obj: Dict[str, Any]) -> Dict[str, Any]:
    questions = obj.get("questions")
    if not isinstance(questions, list):
        return obj

    questions = filter_out_similar_questions(questions)
    random.shuffle(questions)

    for idx, q in enumerate(questions, start=1):
        q["id"] = idx

    obj["questions"] = questions
    return obj


def _word_stem(value: str) -> str:
    word = norm_ws(value).lower()
    word = re.sub(r"[^a-zæøå]", "", word)
    for suffix in ("erne", "ene", "ne", "er", "r"):
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def _coordinated_terms(question: str) -> tuple[str, str] | None:
    stop_words = {
        "det", "den", "der", "dig", "din", "dit", "dine", "du", "en", "er",
        "et", "for", "har", "hvad", "hvis", "kan", "man", "med", "når",
        "og", "om", "skal", "som", "til", "være",
    }
    text = norm_ws(question).lower()
    for match in re.finditer(r"\b([a-zæøå]{4,})\s+og\s+([a-zæøå]{4,})\b", text):
        first_stem = _word_stem(match.group(1))
        second_stem = _word_stem(match.group(2))
        if first_stem in stop_words or second_stem in stop_words:
            continue
        if len(first_stem) < 3 or len(second_stem) < 3 or first_stem == second_stem:
            continue
        return first_stem, second_stem
    return None


def _replace_single_scoped_term_with_dem(text: str, first_stem: str, second_stem: str) -> str:
    if re.search(r"\b(dem|begge|både)\b", text, flags=re.IGNORECASE):
        return text

    words = re.findall(r"[A-Za-zÆØÅæøå]+", text)
    stems = {_word_stem(word) for word in words}
    has_first = first_stem in stems
    has_second = second_stem in stems

    if has_first == has_second:
        return text

    target_stem = first_stem if has_first else second_stem
    replaced = False

    def replace_word(match: re.Match[str]) -> str:
        nonlocal replaced
        if not replaced and _word_stem(match.group(0)) == target_stem:
            replaced = True
            return "dem"
        return match.group(0)

    return re.sub(r"\b[A-Za-zÆØÅæøå]+\b", replace_word, text)


def clean_answer_scope_for_coordinated_questions(obj: Dict[str, Any]) -> Dict[str, Any]:
    questions = obj.get("questions", []) if isinstance(obj, dict) else []
    if not isinstance(questions, list):
        return obj

    for q in questions:
        if str(q.get("origin", "generated")).lower() == "example":
            continue

        terms = _coordinated_terms(str(q.get("question", "")))
        if not terms:
            continue

        first_stem, second_stem = terms
        options = q.get("options", [])
        if isinstance(options, list):
            q["options"] = [
                _replace_single_scoped_term_with_dem(str(option), first_stem, second_stem)
                for option in options
            ]
        q["correct_answer"] = _replace_single_scoped_term_with_dem(
            str(q.get("correct_answer", "")),
            first_stem,
            second_stem,
        )

    return normalize_quiz_obj(obj)


def simplify_generated_language_for_age(obj: Dict[str, Any], params: QuizGenParams) -> Dict[str, Any]:
    if params.age_group.upper() not in {"A", "B"}:
        return obj

    replacements = {
        r"\binhalationssprayen\b": "sprayen",
        r"\binhalationsspray\b": "spray",
    }
    questions = obj.get("questions", []) if isinstance(obj, dict) else []
    if not isinstance(questions, list):
        return obj

    for q in questions:
        if str(q.get("origin", "generated")).lower() == "example":
            continue

        for field in ("question", "correct_answer", "explanation", "topic"):
            value = str(q.get(field, ""))
            for pattern, replacement in replacements.items():
                value = re.sub(pattern, replacement, value, flags=re.IGNORECASE)
            q[field] = value

        options = q.get("options", [])
        if isinstance(options, list):
            cleaned_options = []
            for option in options:
                value = str(option)
                for pattern, replacement in replacements.items():
                    value = re.sub(pattern, replacement, value, flags=re.IGNORECASE)
                cleaned_options.append(value)
            q["options"] = cleaned_options

    return normalize_quiz_obj(obj)


def clean_question_form_for_answer_type(obj: Dict[str, Any]) -> Dict[str, Any]:
    questions = obj.get("questions", []) if isinstance(obj, dict) else []
    if not isinstance(questions, list):
        return obj

    for q in questions:
        if str(q.get("origin", "generated")).lower() == "example":
            continue

        question = norm_ws(str(q.get("question", "")))
        options = [norm_ws(str(option)) for option in q.get("options", [])]
        if len(options) != 3:
            continue

        option_forms = [_answer_form(option) for option in options]
        if question.lower().startswith("hvordan skal du") and all(
            form in {"time_amount", "situation"} for form in option_forms
        ):
            rest = norm_ws(re.sub(r"^hvordan\s+skal\s+du\s+", "", question, flags=re.IGNORECASE)).rstrip("?")
            if rest:
                q["question"] = f"Hvornår skal du {rest}?"
                question = q["question"]

        following_action_match = re.match(
            r"^hvilk(?:et|en)\s+af\s+følgende\s+skal\s+du\s+gøre,?\s+hvis\s+(.+?)\??$",
            question,
            flags=re.IGNORECASE,
        )
        if following_action_match:
            condition = norm_ws(following_action_match.group(1))
            if condition:
                q["question"] = f"Hvad skal du gøre, hvis {condition}?"
                question = q["question"]

        if question.lower().startswith("hvis ") and re.search(r",?\s*skal du også\s+", question, flags=re.IGNORECASE):
            condition = norm_ws(re.sub(r"^hvis\s+", "", question, flags=re.IGNORECASE))
            condition = re.sub(r",?\s*skal du også.*$", "", condition, flags=re.IGNORECASE).strip()
            if condition:
                q["question"] = f"Hvad skal du gøre, hvis {condition}?"
                question = q["question"]

        remember_frequency_match = re.match(
            r"^husk at (.+?) hvor mange gange om (dagen|ugen|måneden)\??$",
            question,
            flags=re.IGNORECASE,
        )
        if remember_frequency_match:
            action = norm_ws(remember_frequency_match.group(1))
            period = remember_frequency_match.group(2).lower()
            q["question"] = f"Hvor mange gange om {period} skal du {action}?"
            question = q["question"]

        forms = [_answer_form(option) for option in options]
        if question.lower().startswith("hvad sker der") and forms.count("action") >= 2:
            condition_match = re.search(r"\bhvis\b(.+?)\?$", question, flags=re.IGNORECASE)
            if condition_match:
                condition = norm_ws(condition_match.group(1))
                q["question"] = f"Hvad skal du gøre, hvis {condition}?"
            else:
                q["question"] = "Hvad skal du gøre?"

        if not question.endswith("?"):
            if question.lower().startswith("hvis ") and forms.count("action") >= 2:
                condition = norm_ws(re.sub(r"^hvis\s+", "", question, flags=re.IGNORECASE))
                condition = re.sub(r",?\s*skal du også.*$", "", condition, flags=re.IGNORECASE).strip()
                if condition:
                    q["question"] = f"Hvad skal du gøre, hvis {condition}?"
            elif re.search(r"\bhvor mange gange\b", question, flags=re.IGNORECASE):
                q["question"] = question.rstrip(".") + "?"

    return normalize_quiz_obj(obj)


def clean_over_absolute_time_answers(obj: Dict[str, Any]) -> Dict[str, Any]:
    questions = obj.get("questions", []) if isinstance(obj, dict) else []
    if not isinstance(questions, list):
        return obj

    for q in questions:
        if str(q.get("origin", "generated")).lower() == "example":
            continue

        question = norm_ws(str(q.get("question", ""))).lower()
        explanation = norm_ws(str(q.get("explanation", ""))).lower()
        if not question.startswith("hvornår kan") and not any(word in explanation for word in ["ofte", "kan"]):
            continue

        options = q.get("options", [])
        answer_index = q.get("answer_index")
        if not isinstance(options, list) or answer_index not in [0, 1, 2]:
            continue

        correct = norm_ws(str(q.get("correct_answer", "")))
        if not correct.lower().startswith("kun "):
            continue

        cleaned_correct = re.sub(r"^\s*kun\s+", "", correct, flags=re.IGNORECASE).strip()
        q["correct_answer"] = cleaned_correct
        options[answer_index] = cleaned_correct

    return normalize_quiz_obj(obj)


def _action_stems(text: str) -> set[str]:
    stems_by_forms = {
        "aflæs": ["aflæs", "aflæse"],
        "brug": ["brug", "bruge", "bruger"],
        "fortsæt": ["fortsæt", "fortsætte", "fortsætter"],
        "hold": ["hold", "holde"],
        "kontakt": ["kontakt", "kontakte", "kontakter"],
        "lad": ["lad", "lade"],
        "lig": ["lig", "ligge", "ligger"],
        "mål": ["mål", "måle", "måler"],
        "noter": ["noter", "notere", "noterer", "notér", "notér"],
        "pust": ["pust", "puste"],
        "prøv": ["prøv", "prøve"],
        "ryst": ["ryst", "ryste", "ryster", "rystes"],
        "sid": ["sid", "sidde", "sidder"],
        "sæt": ["sæt", "sætte", "sætter"],
        "skriv": ["skriv", "skrive"],
        "stå": ["stå", "står"],
        "stop": ["stop", "stoppe", "stopper"],
        "tag": ["tag", "tage", "tager"],
        "vask": ["vask", "vaske"],
        "åbn": ["åbn", "åbne"],
    }
    words = set(re.findall(r"[a-zæøå]+", text.lower()))
    stems = set()
    for stem, forms in stems_by_forms.items():
        if any(form in words for form in forms):
            stems.add(stem)
    return stems


def _is_incomplete_action_option(text: str) -> bool:
    option = norm_ws(text).lower()
    words = re.findall(r"[a-zæøå0-9]+", option)
    if len(words) < 3:
        return False

    prepositions_or_adverbs = {
        "af", "efter", "fast", "før", "hen", "hos", "i", "igennem", "ind",
        "med", "ned", "om", "op", "over", "på", "sammen", "til", "ud",
    }
    needs_direction = {"sæt", "sætte", "stil", "stille", "læg", "lægge", "put", "putte"}
    if words[0] in needs_direction and not any(word in prepositions_or_adverbs for word in words[1:]):
        return True

    if option.startswith(("tage et stort ", "tage en stor ")):
        return True

    unnatural_phrases = [
        "lade vejret i sig",
        "tage et stort blod",
        "tage en stor blod",
    ]
    return any(phrase in option for phrase in unnatural_phrases)


def _looks_like_color_option(text: str) -> bool:
    option = norm_ws(text).lower()
    color_words = {
        "blå", "grøn", "rød", "lilla", "orange", "brun", "gul",
        "orange/brun", "orange/brune", "røde", "grønne", "blå",
    }
    words = set(re.findall(r"[a-zæøå/]+", option))
    return bool(words & color_words) and len(words - color_words - {"farve", "zonen", "zone"}) <= 1


def _looks_like_symptom_option(text: str) -> bool:
    option = norm_ws(text).lower()
    symptom_phrases = [
        "brysttryk", "trykken i brystet", "forpustet", "hoste", "hoster",
        "piber", "hvæser", "pibende", "hvæsende", "svært ved at trække vejret",
        "træt", "trætte", "åndenød",
    ]
    return any(phrase in option for phrase in symptom_phrases)


def _looks_like_time_or_frequency(text: str) -> bool:
    option = norm_ws(text).lower()
    time_words = [
        "gang", "dag", "uge", "måned", "år", "minut", "time",
        "nat", "natten", "morgen", "morgenen", "middag", "aften",
        "efter", "før", "under", "hver",
    ]
    return any(word in option for word in time_words)


def _answer_form(text: str) -> str:
    value = norm_ws(text).lower()
    words = re.findall(r"[a-zæøå0-9/%]+", value)
    if not words:
        return "empty"

    if _looks_like_color_option(value):
        return "color"
    if re.search(r"\d", value):
        if any(unit in value for unit in ["minut", "time", "dag", "uge", "måned", "år"]):
            return "time_amount"
        return "amount"
    if words[0] in {"når", "hvis", "før", "efter", "om", "kun", "hver", "altid", "aldrig"}:
        return "situation"
    if _action_stems(value) or words[0] in {"stop", "stoppe", "fortsæt", "fortsætte", "kontakte", "sige", "undgå"}:
        return "action"
    if _looks_like_time_or_frequency(value):
        return "time_amount"
    if len(words) <= 3:
        return "short_fact"
    return "statement"


def _expected_answer_forms(question: str) -> set[str]:
    q = norm_ws(question).lower()
    if any(phrase in q for phrase in ["hvilken farve", "hvilken zone", "hvad farve"]):
        return {"color", "short_fact"}
    if q.startswith("hvornår") or "hvornår skal" in q:
        return {"situation", "time_amount"}
    if q.startswith("hvor hurtigt") or q.startswith("hvor længe") or q.startswith("hvor mange") or q.startswith("hvor meget"):
        return {"amount", "time_amount", "short_fact"}
    if q.startswith("hvad skal du vaske") or q.startswith("hvad skal du skylle"):
        return {"short_fact", "statement"}
    if any(phrase in q for phrase in ["hvad skal du gøre", "hvad kan du gøre", "hvad er det første", "hvordan skal du", "hvordan bruger"]):
        return {"action"}
    if q.startswith("hvad sker der") or q.startswith("hvad betyder det"):
        return {"statement", "action", "situation"}
    if q.startswith("hvad kan gøre") or q.startswith("hvad kan forværre"):
        return {"short_fact", "statement", "action"}
    if q.startswith("hvad er") or q.startswith("hvilket") or q.startswith("hvilken"):
        return {"short_fact", "statement", "color", "amount", "time_amount"}
    return set()


def _validate_answer_forms(question: str, options: list[str]) -> str | None:
    if len(options) != 3:
        return None

    forms = [_answer_form(option) for option in options]
    expected = _expected_answer_forms(question)
    if norm_ws(question).lower().startswith("hvornår"):
        bad_forms = {"action", "color"}
        if not any(form in bad_forms for form in forms):
            return None
    if norm_ws(question).lower().startswith("hvornår") and all(
        _looks_like_time_or_frequency(option) or _answer_form(option) == "situation"
        for option in options
    ):
        return None
    if expected and any(form not in expected for form in forms):
        return "Svarmulighedernes form passer ikke til spørgsmålet."

    non_empty_forms = [form for form in forms if form != "empty"]
    if len(set(non_empty_forms)) >= 3:
        return "Svarmulighederne blander for mange forskellige svartypper."

    if len(set(non_empty_forms)) == 2:
        counts = {form: non_empty_forms.count(form) for form in set(non_empty_forms)}
        if 1 in counts.values() and not expected:
            return "Svarmulighederne har ikke samme svarform."

    return None


def _validate_option_theme_consistency(question: str, options: list[str], answer_index: int) -> str | None:
    if answer_index not in [0, 1, 2] or len(options) != 3:
        return None

    q = norm_ws(question).lower()
    correct = norm_ws(str(options[answer_index])).lower()
    wrong_options = [
        norm_ws(str(option)).lower()
        for idx, option in enumerate(options)
        if idx != answer_index
    ]

    if not q.startswith("hvornår"):
        return None

    correct_parts = [part.strip() for part in re.split(r"[,;/]", correct) if part.strip()]
    if len(correct_parts) < 2:
        return None

    generic_time_stems = {
        "før", "eft", "efter", "und", "under", "om", "når", "kun", "hver",
        "dag", "dagen", "nat", "natten", "morgen", "morgenen", "middag",
        "aften", "uge", "ugen", "måned", "måneden",
    }
    correct_anchor_stems = _content_word_stems(correct) - generic_time_stems
    question_stems = _content_word_stems(q)
    shared_context = correct_anchor_stems | question_stems
    if not shared_context:
        return None

    for option in wrong_options:
        option_parts = [part.strip() for part in re.split(r"[,;/]", option) if part.strip()]
        if len(option_parts) >= 2 and not (_content_word_stems(option) & shared_context):
            return "Svarmulighederne passer ikke til samme emne som spørgsmålet."

    return None


def _action_family(text: str) -> str:
    value = norm_ws(text).lower()
    if any(word in value for word in ["skriv", "skrive", "noter", "notere", "registr", "skema"]):
        return "record"
    if any(word in value for word in ["medicin", "inhalator", "sug", "dosis"]):
        return "medicine"
    if any(word in value for word in ["vaske", "rengør", "vand", "blød", "lufttør"]):
        return "maintenance"
    if _action_stems(value):
        return "action"
    return ""


def _content_word_stems(text: str) -> set[str]:
    stop_words = {
        "af", "al", "alle", "at", "de", "den", "der", "det", "dig", "din",
        "dit", "dine", "du", "en", "er", "et", "for", "fra", "gør", "gøre",
        "har", "hvad", "hvis", "i", "ikke", "kan", "med", "når", "og", "om",
        "op", "på", "skal", "som", "til", "ud", "vil", "være",
    }
    stems = set()
    short_content_words = {"løb", "røg"}
    for word in re.findall(r"[a-zæøå0-9]+", norm_ws(text).lower()):
        if (len(word) < 4 and word not in short_content_words) or word in stop_words:
            continue
        stems.add(_word_stem(word))
    return stems


def _question_too_broad_for_answer(question: str, correct: str) -> bool:
    q = norm_ws(question).lower()
    if not any(q.startswith(prefix) for prefix in ["hvad skal du gøre", "hvad kan du gøre", "hvordan skal du"]):
        return False

    correct_words = re.findall(r"[a-zæøå0-9]+", correct.lower())
    if len(correct_words) < 7:
        return False

    question_stems = _content_word_stems(q)
    answer_stems = _content_word_stems(correct)
    if len(answer_stems) < 3:
        return False

    overlap = question_stems & answer_stems
    return len(overlap) / len(answer_stems) < 0.25


def validate_question_answer_focus(obj: Dict[str, Any]) -> str | None:
    questions = obj.get("questions", []) if isinstance(obj, dict) else []
    if not isinstance(questions, list):
        return "questions er ikke en liste."

    broad_question_phrases = [
        "hvad skal du gøre",
        "hvordan bruger du",
        "hvad er rigtigt at gøre",
    ]
    cleaning_words = {"blød", "vaske", "vask", "skylle", "rengøre", "rengøring"}
    unsupported_focus_words = {"spacer", "spaceren", "hylster", "hylsteret"}
    young_maintenance_words = {"blød", "rengør", "rengøre", "lufttørre"}
    medicine_action_words = {"inhalator", "medicin", "hurtigvirkende", "forebyggende", "langtidsvirkende"}
    symptom_words = {
        "brysttryk", "forpustet", "hoster", "hoste", "piber", "træt",
        "trætte", "hvæser", "symptom", "symptomer", "tegn",
    }

    for q in questions:
        if str(q.get("origin", "generated")).lower() == "example":
            continue

        question = norm_ws(str(q.get("question", ""))).lower()
        correct = norm_ws(str(q.get("correct_answer", ""))).lower()
        topic = norm_ws(str(q.get("topic", ""))).lower()
        options = [norm_ws(str(option)).lower() for option in q.get("options", [])]
        combined = " ".join([question, correct, topic, *options])

        if question.startswith("hvad bruges") and any(
            word in question for word in ["hoste", "hoster", "piber", "hvæser", "forpustet", "træt", "uoplagt"]
        ):
            return "Et spørgsmål blander symptomer sammen med hvad medicin bruges til."
        if (
            question.startswith(("hvad skal du gøre", "hvad kan du gøre"))
            and any(phrase in question for phrase in ["ikke hoster", "ingen natlig hoste", "ingen daglige symptomer", "ikke har symptomer"])
            and any(word in correct for word in medicine_action_words)
        ):
            return "Et kontroltegn bliver fejlagtigt gjort til et behandlingsspørgsmål."
        if any(word in combined for word in unsupported_focus_words):
            return "Et spørgsmål bruger et emne uden for læringsfokus."
        if (
            any(word in combined for word in young_maintenance_words)
            and not re.search(r"\bpeak\s*flow\b|\bpeakflow", combined)
        ):
            return "Et spørgsmål bruger vedligeholdelse/rengøring som quiz-emne."
        if any(_is_incomplete_action_option(option) for option in options):
            return "En svarmulighed er en ufuldstændig eller unaturlig handling."
        if options and all(_looks_like_color_option(option) for option in options):
            asks_for_color = any(word in question for word in ["farve", "zone", "zonen"])
            if not asks_for_color:
                return "Svarmulighederne er farver, men spørgsmålet spørger ikke efter en farve eller zone."
        answer_form_err = _validate_answer_forms(question, options)
        if answer_form_err:
            return answer_form_err
        theme_err = _validate_option_theme_consistency(
            question,
            options,
            int(q.get("answer_index", -1)) if str(q.get("answer_index", "")).isdigit() else -1,
        )
        if theme_err:
            return theme_err
        if _question_too_broad_for_answer(question, correct):
            return "Spørgsmålet er for bredt i forhold til det konkrete korrekte svar."

        question_actions = _action_stems(question)
        correct_actions = _action_stems(correct)
        answer_stems = _content_word_stems(correct)
        question_answer_overlap = (
            len(_content_word_stems(question) & answer_stems) / len(answer_stems)
            if answer_stems else 0.0
        )
        if (
            len(correct_actions) >= 2
            and question_actions
            and not correct_actions.issubset(question_actions)
            and question_answer_overlap < 0.25
            and not any(phrase in question for phrase in broad_question_phrases)
        ):
            return "Et spørgsmål spørger for snævert i forhold til det korrekte svar."

        question_and_correct = f"{question} {correct}"
        if not any(word in question_and_correct for word in cleaning_words):
            for option in options:
                if any(word in option for word in cleaning_words):
                    return "En svarmulighed hører til en anden procedure end spørgsmålet."

        if question.startswith("hvornår skal du") and not any(word in question for word in symptom_words):
            symptom_like_options = sum(
                any(word in option for word in symptom_words)
                for option in options
            )
            if symptom_like_options >= 2:
                return "Et hvornår-spørgsmål bruger symptomer som tilfældige svarmuligheder."

        if "opleve" in question and "symptom" in topic:
            return "Et opleve-spørgsmål er for bredt til symptom-svar."

        symptom_context_words = {"astma", "symptom", "symptomer", "tegn", "vejrtrækning", "hoste", "hoster", "piber", "hvæser", "forpustet"}
        if (
            any(word in combined for word in ["træt", "trætte", "uoplagt"])
            and not any(word in question for word in symptom_context_words)
        ):
            return "Et symptom-spørgsmål mangler tydelig astma-kontekst."

        asks_for_symptom = any(phrase in question for phrase in ["symptom", "tegn på astma", "tegn på"])
        if asks_for_symptom and sum(_looks_like_symptom_option(option) for option in options) >= 2:
            return "Et symptom-spørgsmål har flere svarmuligheder, der kan være korrekte."

        if question.startswith("hvornår kan") and correct.startswith("kun ") and "ofte" in str(q.get("explanation", "")).lower():
            return "Det korrekte svar er for absolut i forhold til forklaringen."

    return None


def validate_precise_terms_against_material(obj: Dict[str, Any], source_material: str | None) -> str | None:
    if not source_material:
        return None

    material = norm_ws(source_material).lower()
    questions = obj.get("questions", []) if isinstance(obj, dict) else []
    if not isinstance(questions, list):
        return "questions er ikke en liste."

    contrast_groups = [
        {"kold", "koldt", "lunkent", "varmt"},
        {"dag", "uge", "måned"},
        {"blå", "grøn", "rød", "lilla", "orange", "brun", "gul", "orange/brun"},
    ]

    for q in questions:
        if str(q.get("origin", "generated")).lower() == "example":
            continue

        answer_text = " ".join(
            [
                str(q.get("correct_answer", "")),
                str(q.get("explanation", "")),
            ]
        ).lower()

        for group in contrast_groups:
            material_terms = {term for term in group if re.search(rf"\b{re.escape(term)}\b", material)}
            answer_terms = {term for term in group if re.search(rf"\b{re.escape(term)}\b", answer_text)}
            if len(material_terms) == 1 and answer_terms and not answer_terms <= material_terms:
                return "Et præcist ord i svaret matcher ikke materialet."

    return None


def _best_matching_support_sentence(material: str, q: Dict[str, Any]) -> str:
    sentences = re.split(r"(?=\b(?:- )?Side\s+\d+\s*:|\[(?:OCR\s+)?Side\s+\d+\])|(?<=[\.\?!])\s+|\n+", material)
    query = " ".join(
        [
            str(q.get("question", "")),
            str(q.get("topic", "")),
        ]
    )
    query_stems = _content_word_stems(query)
    best_sentence = ""
    best_score = 0
    for sentence in sentences:
        clean = norm_ws(sentence)
        if not clean:
            continue
        sentence_stems = _content_word_stems(clean)
        score = len(query_stems & sentence_stems)
        if score > best_score:
            best_score = score
            best_sentence = clean
    return best_sentence


def _source_page_from_text(text: str) -> int:
    match = re.search(r"\bSide\s+(\d+)\b", text or "", flags=re.IGNORECASE)
    if match:
        return int(match.group(1))
    return 0


def clean_source_page_from_material(obj: Dict[str, Any], source_material: str | None) -> Dict[str, Any]:
    if not source_material:
        return obj

    questions = obj.get("questions", []) if isinstance(obj, dict) else []
    if not isinstance(questions, list):
        return obj

    for q in questions:
        if str(q.get("origin", "generated")).lower() == "example":
            continue

        support_sentence = _best_matching_material_sentence(source_material, q)
        source_page = _source_page_from_text(support_sentence)
        if source_page:
            q["source_page"] = source_page

    return normalize_quiz_obj(obj)


def validate_correct_answer_supported_by_material(obj: Dict[str, Any], source_material: str | None) -> str | None:
    if not source_material:
        return None

    material_stems = _content_word_stems(source_material)
    if not material_stems:
        return None

    questions = obj.get("questions", []) if isinstance(obj, dict) else []
    if not isinstance(questions, list):
        return "questions er ikke en liste."

    allowed_generated_stems = {
        "astma", "ekstra", "medicin", "peak", "flow", "peakflow",
    }
    time_stems = {
        "morgen", "morgenen", "middag", "natten", "nat", "aften",
        "dagen", "dag", "uge", "ugen", "måned", "måneden",
        "minut", "minutter", "time", "timer",
    }

    for q in questions:
        if str(q.get("origin", "generated")).lower() == "example":
            continue

        correct_stems = _content_word_stems(str(q.get("correct_answer", "")))
        if not correct_stems:
            continue

        support_sentence = _best_matching_support_sentence(source_material, q)
        support_stems = _content_word_stems(support_sentence) or material_stems

        question = norm_ws(str(q.get("question", ""))).lower()
        if question.startswith("hvornår") and correct_stems & time_stems and not (correct_stems & support_stems):
            return "Det korrekte tidspunkt passer ikke til den PDF-linje spørgsmålet bygger på."

        explanation_stems = _content_word_stems(str(q.get("explanation", "")))
        extra_explanation_times = (explanation_stems & time_stems) - support_stems - correct_stems
        if extra_explanation_times:
            return "Forklaringen tilføjer præcis tid, som ikke står i PDF-punktet."

        unsupported = correct_stems - material_stems - allowed_generated_stems
        if unsupported:
            return "Det korrekte svar bruger ord, der ikke ser ud til at komme fra PDF-materialet."

    return None


def clean_short_frequency_answers_from_material(obj: Dict[str, Any], source_material: str | None) -> Dict[str, Any]:
    if not source_material:
        return obj

    material = norm_ws(source_material)
    questions = obj.get("questions", []) if isinstance(obj, dict) else []
    if not isinstance(questions, list):
        return obj

    for q in questions:
        if str(q.get("origin", "generated")).lower() == "example":
            continue

        options = q.get("options", [])
        answer_index = q.get("answer_index")
        if not isinstance(options, list) or answer_index not in [0, 1, 2]:
            continue

        correct = norm_ws(str(q.get("correct_answer", "")))
        short_match = re.fullmatch(r"(\d+)\s*gang(?:e)?", correct, flags=re.IGNORECASE)
        if not short_match:
            continue

        count = short_match.group(1)
        full_match = re.search(
            rf"\b{re.escape(count)}\s*gang(?:e)?\s+om\s+(?:ugen|uge|måneden|måned|dagen|dag)(?:\s+i\s+[A-Za-zÆØÅæøå ]+?vand)?\b",
            material,
            flags=re.IGNORECASE,
        )
        if not full_match:
            continue

        full_answer = norm_ws(full_match.group(0))
        suffix = norm_ws(full_answer[len(full_match.group(0).split()[0]):])
        short_prefix_match = re.match(r"^\d+\s*gang(?:e)?", full_answer, flags=re.IGNORECASE)
        suffix = full_answer[short_prefix_match.end():].strip() if short_prefix_match else ""
        q["correct_answer"] = full_answer
        options[answer_index] = full_answer
        if suffix:
            for idx, option in enumerate(options):
                if idx == answer_index:
                    continue
                option_text = norm_ws(str(option))
                if re.fullmatch(r"\d+\s*gang(?:e)?", option_text, flags=re.IGNORECASE):
                    options[idx] = f"{option_text} {suffix}"
        explanation = norm_ws(str(q.get("explanation", "")))
        if explanation:
            q["explanation"] = re.sub(
                rf"\b{re.escape(correct)}\b",
                full_answer,
                explanation,
                flags=re.IGNORECASE,
            )

    return normalize_quiz_obj(obj)


def clean_precise_terms_from_material(obj: Dict[str, Any], source_material: str | None) -> Dict[str, Any]:
    if not source_material:
        return obj

    material = norm_ws(source_material).lower()
    questions = obj.get("questions", []) if isinstance(obj, dict) else []
    if not isinstance(questions, list):
        return obj

    contrast_groups = [
        {"koldt", "lunkent", "varmt"},
        {"dag", "uge", "måned"},
        {"blå", "grøn", "rød", "lilla", "orange", "brun", "gul", "orange/brun"},
    ]

    def replace_terms(text: str, group: set[str], correct_term: str) -> str:
        output = str(text)
        for term in sorted(group - {correct_term}, key=len, reverse=True):
            output = re.sub(
                rf"\b{re.escape(term)}\b",
                correct_term,
                output,
                flags=re.IGNORECASE,
            )
        return output

    for q in questions:
        if str(q.get("origin", "generated")).lower() == "example":
            continue

        options = q.get("options", [])
        answer_index = q.get("answer_index")
        if not isinstance(options, list) or answer_index not in [0, 1, 2]:
            continue

        for group in contrast_groups:
            material_terms = {term for term in group if re.search(rf"\b{re.escape(term)}\b", material)}
            if len(material_terms) != 1:
                continue

            correct_term = next(iter(material_terms))
            q["correct_answer"] = replace_terms(str(q.get("correct_answer", "")), group, correct_term)
            q["explanation"] = replace_terms(str(q.get("explanation", "")), group, correct_term)
            options[answer_index] = replace_terms(str(options[answer_index]), group, correct_term)

    return normalize_quiz_obj(obj)


def _best_matching_material_sentence(material: str, q: Dict[str, Any]) -> str:
    sentences = re.split(r"(?<=[\.\?!])\s+|\n+", material)
    query = " ".join(
        [
            str(q.get("question", "")),
            str(q.get("correct_answer", "")),
            str(q.get("topic", "")),
        ]
    )
    query_stems = _content_word_stems(query)
    best_sentence = ""
    best_score = 0
    for sentence in sentences:
        clean = norm_ws(sentence)
        if not clean:
            continue
        sentence_stems = _content_word_stems(clean)
        score = len(query_stems & sentence_stems)
        if score > best_score:
            best_score = score
            best_sentence = clean
    return best_sentence


def clean_modal_verbs_from_material(obj: Dict[str, Any], source_material: str | None) -> Dict[str, Any]:
    if not source_material:
        return obj

    questions = obj.get("questions", []) if isinstance(obj, dict) else []
    if not isinstance(questions, list):
        return obj

    modals = {"skal", "kan", "må", "bør"}
    for q in questions:
        if str(q.get("origin", "generated")).lower() == "example":
            continue

        material_sentence = _best_matching_material_sentence(source_material, q).lower()
        material_modals = {
            modal for modal in modals
            if re.search(rf"\b{re.escape(modal)}\b", material_sentence)
        }
        if len(material_modals) != 1:
            continue

        correct_modal = next(iter(material_modals))
        question = str(q.get("question", ""))
        generated_modals = {
            modal for modal in modals
            if re.search(rf"\b{re.escape(modal)}\b", question.lower())
        }
        if generated_modals and generated_modals != {correct_modal}:
            for modal in sorted(generated_modals - {correct_modal}, key=len, reverse=True):
                question = re.sub(
                    rf"\b{re.escape(modal)}\b",
                    correct_modal,
                    question,
                    flags=re.IGNORECASE,
                )
            q["question"] = question

        for field in ("correct_answer", "explanation"):
            value = str(q.get(field, ""))
            for modal in sorted(modals - {correct_modal}, key=len, reverse=True):
                value = re.sub(
                    rf"\b{re.escape(modal)}\b",
                    correct_modal,
                    value,
                    flags=re.IGNORECASE,
                )
            q[field] = value

    return normalize_quiz_obj(obj)


def renumber_questions(questions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for idx, q in enumerate(questions, start=1):
        item = dict(q)
        item["id"] = idx
        out.append(item)
    return out


def ollama_chat(
    model: str,
    messages: List[Dict[str, str]],
    temperature: float = 0.2,
    timeout: int = 180,
    num_ctx: int = 4096,
    num_predict: int = 1200,
) -> str:
    chat_payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "format": "json",
        "options": {
            "temperature": temperature,
            "num_ctx": num_ctx,
            "num_predict": num_predict,
        },
    }

    try:
        response = requests.post(OLLAMA_CHAT_URL, json=chat_payload, timeout=timeout)
    except requests.Timeout as e:
        stop_ollama_model(model)
        raise RuntimeError(
            f"Ollama brugte for lang tid på modellen '{model}' og blev stoppet. "
            "Prøv færre genererede ekstra-spørgsmål eller et mindre sideinterval."
        ) from e
    if response.status_code == 404:
        body = response.text.strip()
        if "model" in body.lower() and ("not found" in body.lower() or "pull" in body.lower()):
            raise RuntimeError(
                f"Ollama kan ikke finde modellen '{model}'. Kør først: ollama pull {model}"
            )

        prompt = "\n\n".join(
            f"{message.get('role', 'user').upper()}:\n{message.get('content', '')}"
            for message in messages
        )
        generate_payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {
                "temperature": temperature,
                "num_ctx": num_ctx,
                "num_predict": num_predict,
            },
        }
        try:
            response = requests.post(OLLAMA_GENERATE_URL, json=generate_payload, timeout=timeout)
        except requests.Timeout as e:
            stop_ollama_model(model)
            raise RuntimeError(
                f"Ollama brugte for lang tid på modellen '{model}' og blev stoppet. "
                "Prøv færre genererede ekstra-spørgsmål eller et mindre sideinterval."
            ) from e

    try:
        response.raise_for_status()
    except requests.HTTPError as e:
        body = response.text.strip()
        if response.status_code == 404 and "model" in body.lower():
            raise RuntimeError(
                f"Ollama kan ikke finde modellen '{model}'. Kør først: ollama pull {model}"
            ) from e
        raise RuntimeError(f"Ollama-fejl {response.status_code}: {body or str(e)}") from e

    data = response.json()
    if "message" in data:
        return data["message"]["content"]
    return data.get("response", "")


def stop_ollama_model(model: str) -> None:
    try:
        subprocess.run(
            ["ollama", "stop", model],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except Exception:
        pass


def extract_material_from_pdf(pdf_bytes: bytes, params: QuizGenParams) -> Tuple[str, bool]:
    material_parts: List[str] = []
    pages_needing_ocr: List[int] = []

    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        total_pages = len(pdf.pages)

        start = 1 if params.page_from is None else max(1, params.page_from)
        end = total_pages if params.page_to is None else min(total_pages, params.page_to)
        end = min(end, params.max_pages)

        if start > end:
            raise RuntimeError("Ugyldigt sideinterval.")

        for i in range(start, end + 1):
            page = pdf.pages[i - 1]
            text = (page.extract_text() or "").strip()
            clean_text = norm_ws(text)

            if len(clean_text) >= 80:
                material_parts.append(f"[Side {i}]\n{text}")
            elif 15 <= len(clean_text) < 80:
                material_parts.append(f"[Side {i}]\n{text}")
                pages_needing_ocr.append(i)
            else:
                pages_needing_ocr.append(i)

    used_ocr = False
    max_ocr_pages = min(params.ocr.max_pages, 15)
    pages_needing_ocr = pages_needing_ocr[:max_ocr_pages]

    if params.ocr.use_ocr and OCR_AVAILABLE and pages_needing_ocr:
        for page_no in pages_needing_ocr:
            try:
                try:
                    ocr_text = extract_text_ocr(
                        pdf_bytes=pdf_bytes,
                        page_from=page_no,
                        page_to=page_no,
                        max_pages=page_no,
                        dpi=params.ocr.dpi,
                        lang=params.ocr.lang,
                    )
                except Exception:
                    if params.ocr.lang.lower() == "eng":
                        raise
                    ocr_text = extract_text_ocr(
                        pdf_bytes=pdf_bytes,
                        page_from=page_no,
                        page_to=page_no,
                        max_pages=page_no,
                        dpi=params.ocr.dpi,
                        lang="eng",
                    )

                ocr_clean = norm_ws(ocr_text)
                if len(ocr_clean) < 30:
                    continue

                material_parts.append(f"[OCR Side {page_no}]\n{ocr_text}")
                used_ocr = True

            except Exception:
                continue

    material = "\n\n".join(material_parts).strip()

    if not material:
        raise RuntimeError("Kunne ikke udtrække brugbar tekst fra PDF.")

    return material[: params.max_chars], used_ocr


def extract_facts_from_material(material: str, params: QuizGenParams, max_facts: int = 20) -> Dict[str, Any]:
    minimum_usable_facts = min(max(params.num_questions, 5), max_facts)
    fact_token_budget = min(2600, max(1800, max_facts * 180))

    facts_output = ollama_chat(
        model=params.model,
        messages=[
            {"role": "system", "content": "Returnér kun gyldig JSON. Skriv alt på dansk."},
            {
                "role": "user",
                "content": build_fact_extraction_prompt(material, params, max_facts=max_facts),
            },
        ],
        temperature=0.1,
        timeout=params.timeout,
        num_ctx=params.num_ctx,
        num_predict=fact_token_budget,
    )

    facts_obj = safe_json_loads(facts_output)
    if not facts_obj:
        salvaged_facts = salvage_json_array_items(facts_output, "facts")
        if len(salvaged_facts) >= minimum_usable_facts:
            facts_obj = {"facts": salvaged_facts[:max_facts]}

    if not facts_obj:
        repaired_facts_output = ollama_chat(
            model=params.model,
            messages=[
                {"role": "system", "content": "Returnér kun gyldig JSON. Skriv alt på dansk."},
                {
                    "role": "user",
                    "content": build_fact_repair_prompt(facts_output, max_facts=max_facts),
                },
            ],
            temperature=0.0,
            timeout=params.timeout,
            num_ctx=params.num_ctx,
            num_predict=fact_token_budget,
        )
        facts_obj = safe_json_loads(repaired_facts_output)
        if not facts_obj:
            salvaged_facts = salvage_json_array_items(repaired_facts_output, "facts")
            if len(salvaged_facts) >= minimum_usable_facts:
                facts_obj = {"facts": salvaged_facts[:max_facts]}

        if not facts_obj:
            compact_facts_output = ollama_chat(
                model=params.model,
                messages=[
                    {"role": "system", "content": "Returnér kun gyldig JSON. Skriv alt på dansk."},
                    {
                        "role": "user",
                        "content": build_fact_extraction_prompt(
                            material,
                            params,
                            max_facts=minimum_usable_facts,
                        ),
                    },
                ],
                temperature=0.0,
                timeout=params.timeout,
                num_ctx=params.num_ctx,
                num_predict=max(1400, minimum_usable_facts * 220),
            )
            facts_obj = safe_json_loads(compact_facts_output)
            if not facts_obj:
                salvaged_facts = salvage_json_array_items(compact_facts_output, "facts")
                if len(salvaged_facts) >= minimum_usable_facts:
                    facts_obj = {"facts": salvaged_facts[:minimum_usable_facts]}

            if not facts_obj:
                preview = norm_ws(facts_output)[:500]
                raise RuntimeError(f"Kunne ikke parse facts-JSON fra modellen. Første del af svaret var: {preview}")

    facts_obj = normalize_facts_obj(facts_obj)
    facts = facts_obj.get("facts", [])

    if not facts:
        raise RuntimeError("Ingen brugbare facts blev udtrukket fra materialet.")
    if len(facts) < minimum_usable_facts:
        raise RuntimeError(
            f"Der blev kun udtrukket {len(facts)} brugbare facts, "
            f"men der skal bruges mindst {minimum_usable_facts}. "
            "Prøv lidt færre spørgsmål eller et mindre sideinterval."
        )

    return facts_obj


def run_review_step(
    quiz_obj: Dict[str, Any],
    material: str,
    params: QuizGenParams,
    expected_count: int,
    facts_obj: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    reviewed_output = ollama_chat(
        model=params.model,
        messages=[
            {"role": "system", "content": "Returnér kun gyldig JSON. Skriv alt på dansk."},
            {
                "role": "user",
                "content": build_review_prompt(quiz_obj, material, params, expected_count, facts_obj=facts_obj),
            },
        ],
        temperature=0.0,
        timeout=params.timeout,
        num_ctx=params.num_ctx,
        num_predict=1000,
    )

    obj = safe_json_loads(reviewed_output)
    if not obj:
        return None

    obj = normalize_quiz_obj(obj)
    obj = shuffle_questions(obj)
    return obj


def validate_basic_quiz(obj: Optional[Dict[str, Any]]) -> str | None:
    err = validate_quiz_schema(obj) if obj else "Kunne ikke parse JSON."
    if not err and obj:
        err = validate_mcq_quality(obj)
    if not err and obj:
        err = validate_danish_language_quality(obj)
    return err


def validate_generated_quiz(obj: Optional[Dict[str, Any]], params: QuizGenParams) -> str | None:
    err = validate_basic_quiz(obj)
    if not err and obj:
        err = validate_source_pages(obj, params.page_from, params.page_to)
    if not err and obj:
        err = validate_age_group_content(obj, params.age_group)
    if not err and obj:
        err = validate_question_answer_focus(obj)
    if not err and obj:
        err = validate_option_set_diversity(obj)
    return err


def clean_silly_wrong_options(obj: Dict[str, Any], params: QuizGenParams) -> Dict[str, Any]:
    replacements_by_age = {
        "A": ["Du får ondt i foden", "Du bliver sulten", "Du får lyst til at tegne"],
        "B": ["Du får ondt i foden", "Du bliver sulten", "Du får lyst til at læse"],
        "C": ["Hovedpine", "Mavesmerter", "Træthed efter for lidt søvn"],
        "UNG": ["Hovedpine", "Mavesmerter", "Almindelig muskelømhed"],
    }
    bad_phrases = ["superkræfter", "længere hår", "ondt i tåen", "ondt i armen"]
    replacements = replacements_by_age.get(params.age_group.upper(), replacements_by_age["B"])

    questions = obj.get("questions", []) if isinstance(obj, dict) else []
    if not isinstance(questions, list):
        return obj

    for q in questions:
        if str(q.get("origin", "generated")).lower() == "example":
            continue

        options = q.get("options", [])
        answer_index = q.get("answer_index")
        if not isinstance(options, list) or answer_index not in [0, 1, 2]:
            continue

        correct = norm_ws(str(options[answer_index])).lower()
        used = {correct}
        for idx, option in enumerate(options):
            option_text = norm_ws(str(option))
            option_key = option_text.lower()
            if idx == answer_index:
                if any(phrase in option_key for phrase in bad_phrases):
                    return obj
                continue

            if not any(phrase in option_key for phrase in bad_phrases):
                used.add(option_key)
                continue

            replacement = next(
                (
                    value for value in replacements
                    if norm_ws(value).lower() not in used
                    and norm_ws(value).lower() != correct
                ),
                "Det handler ikke om vejrtrækning",
            )
            options[idx] = replacement
            used.add(norm_ws(replacement).lower())

    return normalize_quiz_obj(obj)


def clean_mismatched_wrong_options(obj: Dict[str, Any]) -> Dict[str, Any]:
    return obj


def _join_danish_list(parts: list[str]) -> str:
    parts = [norm_ws(part) for part in parts if norm_ws(part)]
    if len(parts) <= 1:
        return parts[0] if parts else ""
    if len(parts) == 2:
        return f"{parts[0]} og {parts[1]}"
    return f"{', '.join(parts[:-1])} og {parts[-1]}"


def _split_list_answer_parts(value: str) -> list[str]:
    return [
        norm_ws(part).strip(" .")
        for part in re.split(r",|;|/", value or "")
        if norm_ws(part).strip(" .")
    ]


def _normalize_list_answer(parts: list[str]) -> str:
    normalized_parts = [part.lower() for part in parts if norm_ws(part)]
    full_correct = _join_danish_list(normalized_parts)
    return full_correct[:1].upper() + full_correct[1:] if full_correct else ""


def _source_contains_all_parts(source_material: str | None, parts: list[str]) -> bool:
    if not source_material or len(parts) < 2:
        return False
    material = norm_ws(source_material).lower()
    return all(norm_ws(part).lower() in material for part in parts if norm_ws(part))


def clean_multi_part_timing_options(
    obj: Dict[str, Any],
    source_material: str | None = None,
) -> Dict[str, Any]:
    questions = obj.get("questions", []) if isinstance(obj, dict) else []
    if not isinstance(questions, list):
        return obj

    for q in questions:
        if str(q.get("origin", "generated")).lower() == "example":
            continue

        question = norm_ws(str(q.get("question", "")))
        if not question.lower().startswith("hvornår"):
            continue

        options = q.get("options", [])
        answer_index = q.get("answer_index")
        if not isinstance(options, list) or answer_index not in [0, 1, 2] or len(options) != 3:
            continue

        correct = norm_ws(str(q.get("correct_answer", "")))
        if not correct:
            continue

        parts = _split_list_answer_parts(correct)
        if len(parts) < 3:
            option_parts = [norm_ws(str(option)).strip(" .") for option in options if norm_ws(str(option)).strip(" .")]
            all_options_are_timing = all(
                _looks_like_time_or_frequency(option) or _answer_form(option) == "situation"
                for option in option_parts
            )
            if (
                len(option_parts) == 3
                and all_options_are_timing
                and _source_contains_all_parts(source_material, option_parts)
            ):
                parts = option_parts
        if len(parts) < 3:
            continue

        normalized_parts = [part.lower() for part in parts]
        full_correct = _normalize_list_answer(parts)
        wrong_candidates = [
            f"Kun {normalized_parts[0].lower()}",
            f"Kun {normalized_parts[-1].lower()}",
            _join_danish_list(normalized_parts[:-1]),
            _join_danish_list(normalized_parts[1:]),
        ]

        new_options: list[str] = []
        for idx in range(3):
            if idx == answer_index:
                new_options.append(full_correct)
                continue
            replacement = next(
                (
                    candidate for candidate in wrong_candidates
                    if norm_ws(candidate).lower() not in {norm_ws(option).lower() for option in new_options}
                    and norm_ws(candidate).lower() != full_correct.lower()
                ),
                "",
            )
            new_options.append(replacement or norm_ws(str(options[idx])))

        q["options"] = new_options
        q["correct_answer"] = full_correct

        action_match = re.match(r"^hvornår\s+skal\s+du\s+(.+?)\??$", question, flags=re.IGNORECASE)
        if action_match:
            action = norm_ws(action_match.group(1))
            q["explanation"] = f"Du skal {action} {full_correct.lower()}."

    return normalize_quiz_obj(obj)


def _causal_core_stems(text: str) -> set[str]:
    generic = {
        "begrænsning", "aktivitet", "aktiviteter", "svært", "løbe",
        "fordi", "pga", "grund", "skyldes", "problemer", "mange",
    }
    return _content_word_stems(text) - generic


def _causal_wrong_replacements(correct: str, params: QuizGenParams) -> list[str]:
    correct_lower = norm_ws(correct).lower()
    if "pga" in correct_lower or "på grund af" in correct_lower:
        prefix = re.split(r"\bpga\b|\bpå grund af\b", correct, maxsplit=1, flags=re.IGNORECASE)[0].strip()
        prefix = prefix or "Det bliver svært"
        if params.age_group.upper() in {"A", "B"}:
            causes = ["for lidt søvn", "for lidt opvarmning", "løse sko"]
        else:
            causes = ["for lidt søvn", "at du ikke har varmet op", "almindelig muskeltræthed"]
        return [f"{prefix} pga {cause}" for cause in causes]

    if params.age_group.upper() in {"A", "B"}:
        return [
            "Fordi benene bliver længere",
            "Fordi man altid skal løbe hurtigere",
            "Fordi skoene bliver tungere",
        ]
    return [
        "Fordi kroppen får mere energi med det samme",
        "Fordi musklerne automatisk bliver stærkere",
        "Fordi vejrtrækningen ikke betyder noget under aktivitet",
    ]


def clean_causal_wrong_options(obj: Dict[str, Any], params: QuizGenParams) -> Dict[str, Any]:
    questions = obj.get("questions", []) if isinstance(obj, dict) else []
    if not isinstance(questions, list):
        return obj

    for q in questions:
        if str(q.get("origin", "generated")).lower() == "example":
            continue

        question = norm_ws(str(q.get("question", "")))
        if not question.lower().startswith("hvorfor"):
            continue

        options = q.get("options", [])
        answer_index = q.get("answer_index")
        if not isinstance(options, list) or answer_index not in [0, 1, 2] or len(options) != 3:
            continue

        correct = norm_ws(str(q.get("correct_answer", "")))
        if not correct:
            continue

        correct_core = _causal_core_stems(correct)
        replacements = _causal_wrong_replacements(correct, params)
        used = {correct.lower()}
        for idx, option in enumerate(options):
            option_text = norm_ws(str(option))
            option_key = option_text.lower()
            if idx == answer_index:
                options[idx] = correct
                continue

            option_core = _causal_core_stems(option_text)
            too_close = bool(correct_core and option_core and len(correct_core & option_core) / max(1, len(option_core)) >= 0.5)
            mentions_same_cause = any(
                word in option_key
                for word in ["astma", "vejrtrækning", "vejrtrækningsbesvær", "åndenød", "forpustet"]
            )
            malformed = any(phrase in option_key for phrase in ["mangel på træn", "manglende træn"])
            if not too_close and not mentions_same_cause and not malformed:
                used.add(option_key)
                continue

            replacement = next(
                (
                    candidate for candidate in replacements
                    if norm_ws(candidate).lower() not in used
                    and norm_ws(candidate).lower() != correct.lower()
                ),
                "",
            )
            if replacement:
                options[idx] = replacement
                used.add(norm_ws(replacement).lower())

        explanation = norm_ws(str(q.get("explanation", "")))
        if not explanation:
            q["explanation"] = f"Det rigtige svar er {correct.lower()}."

    return normalize_quiz_obj(obj)


def filter_valid_generated_questions(
    obj: Optional[Dict[str, Any]],
    params: QuizGenParams,
    example_obj: Optional[Dict[str, Any]] = None,
    source_material: str | None = None,
) -> Dict[str, Any]:
    questions = obj.get("questions", []) if isinstance(obj, dict) else []
    if not isinstance(questions, list):
        questions = []

    valid_questions: List[Dict[str, Any]] = []
    for q in questions:
        candidate = normalize_quiz_obj({"quiz_title": "Spørgsmålsforslag", "questions": [q]})
        candidate = simplify_generated_language_for_age(candidate, params)
        candidate = clean_answer_scope_for_coordinated_questions(candidate)
        candidate = clean_question_form_for_answer_type(candidate)
        candidate = clean_over_absolute_time_answers(candidate)
        candidate = clean_silly_wrong_options(candidate, params)
        candidate = clean_mismatched_wrong_options(candidate)
        candidate = clean_multi_part_timing_options(candidate, source_material)
        candidate = clean_causal_wrong_options(candidate, params)
        candidate = clean_short_frequency_answers_from_material(candidate, source_material)
        candidate = clean_precise_terms_from_material(candidate, source_material)
        candidate = clean_modal_verbs_from_material(candidate, source_material)
        candidate = clean_source_page_from_material(candidate, source_material)
        err = validate_generated_quiz(candidate, params)
        if not err:
            err = validate_precise_terms_against_material(candidate, source_material)
        if not err:
            err = validate_correct_answer_supported_by_material(candidate, source_material)
        if not err and example_obj:
            err = validate_against_example_answers(candidate, example_obj)
        if not err:
            valid_questions.extend(candidate.get("questions", []))

    cleaned = normalize_quiz_obj(
        {
            "quiz_title": obj.get("quiz_title", "Spørgsmålsforslag") if isinstance(obj, dict) else "Spørgsmålsforslag",
            "questions": valid_questions,
        }
    )
    cleaned["questions"] = filter_out_similar_questions(cleaned.get("questions", []))
    return shuffle_questions(cleaned)


def describe_generated_question_rejections(
    obj: Optional[Dict[str, Any]],
    params: QuizGenParams,
    example_obj: Optional[Dict[str, Any]] = None,
    source_material: str | None = None,
) -> str:
    questions = obj.get("questions", []) if isinstance(obj, dict) else []
    if not isinstance(questions, list) or not questions:
        return "Ingen spørgsmål i modelsvaret."

    notes: List[str] = []
    for idx, q in enumerate(questions[:3], start=1):
        candidate = normalize_quiz_obj({"quiz_title": "Spørgsmålsforslag", "questions": [q]})
        candidate = simplify_generated_language_for_age(candidate, params)
        candidate = clean_answer_scope_for_coordinated_questions(candidate)
        candidate = clean_question_form_for_answer_type(candidate)
        candidate = clean_over_absolute_time_answers(candidate)
        candidate = clean_silly_wrong_options(candidate, params)
        candidate = clean_mismatched_wrong_options(candidate)
        candidate = clean_multi_part_timing_options(candidate, source_material)
        candidate = clean_causal_wrong_options(candidate, params)
        candidate = clean_short_frequency_answers_from_material(candidate, source_material)
        candidate = clean_precise_terms_from_material(candidate, source_material)
        candidate = clean_modal_verbs_from_material(candidate, source_material)
        candidate = clean_source_page_from_material(candidate, source_material)
        err = validate_generated_quiz(candidate, params)
        if not err:
            err = validate_precise_terms_against_material(candidate, source_material)
        if not err:
            err = validate_correct_answer_supported_by_material(candidate, source_material)
        if not err and example_obj:
            err = validate_against_example_answers(candidate, example_obj)
        question_text = norm_ws(str(q.get("question", "")))[:120]
        notes.append(f"{idx}: {err or 'ingen fejl'} ({question_text})")

    return " | ".join(notes)


def usable_question_count(obj: Optional[Dict[str, Any]]) -> int:
    questions = obj.get("questions", []) if isinstance(obj, dict) else []
    return len(questions) if isinstance(questions, list) else 0


def merge_question_sets(base_obj: Dict[str, Any], extra_obj: Dict[str, Any], needed_count: int) -> Dict[str, Any]:
    merged = {
        "quiz_title": base_obj.get("quiz_title") or "Spørgsmålsforslag",
        "questions": [
            *(base_obj.get("questions", []) if isinstance(base_obj.get("questions"), list) else []),
            *(extra_obj.get("questions", []) if isinstance(extra_obj.get("questions"), list) else []),
        ],
    }
    merged = normalize_quiz_obj(merged)
    merged["questions"] = filter_out_similar_questions(merged.get("questions", []))[:needed_count]
    return shuffle_questions(merged)


def examples_to_quiz_obj(params: QuizGenParams) -> Dict[str, Any]:
    examples = get_examples_for_page_range(
        params.age_group,
        page_from=params.page_from,
        page_to=params.page_to,
    )

    questions: List[Dict[str, Any]] = []
    for idx, example in enumerate(examples, start=1):
        options = [str(option).strip() for option in example.get("options", []) if str(option).strip()]
        correct_answer = str(example.get("correct_answer", "")).strip()
        try:
            answer_index = next(
                i for i, option in enumerate(options) if norm_ws(option).lower() == norm_ws(correct_answer).lower()
            )
        except StopIteration:
            answer_index = 0
            if options:
                correct_answer = options[0]

        questions.append(
            {
                "id": idx,
                "type": "mcq",
                "question": str(example.get("question", "")).strip(),
                "options": options,
                "answer_index": answer_index,
                "correct_answer": correct_answer,
                "explanation": "Eksempelspørgsmål baseret på materialet.",
                "source_page": int(example.get("source_page", 0) or 0),
                "source_fact_id": idx,
                "topic": str(example.get("topic", "")).strip() or "eksempel",
                "list_group": str(example.get("list_group", "")).strip()
                or f"{params.age_group}:side-{int(example.get('source_page', 0) or 0)}",
                "difficulty": str(example.get("difficulty", "")).strip() or "easy",
                "origin": "example",
            }
        )

    return normalize_quiz_obj({"quiz_title": "Spørgsmålsforslag", "questions": questions})


def trim_quiz_to_count(obj: Dict[str, Any], count: int) -> Dict[str, Any]:
    obj = dict(obj)
    questions = obj.get("questions", [])
    if isinstance(questions, list) and len(questions) > count:
        obj["questions"] = questions[:count]
    return shuffle_questions(obj)


def compact_material_for_supplement(
    material: str,
    existing_questions: List[Dict[str, Any]],
    params: Optional[QuizGenParams] = None,
    max_chars: int = 1200,
) -> str:
    page_blocks = re.findall(
        r"(\[(?:OCR\s+)?Side\s+\d+\]\n.*?)(?=\n\n\[(?:OCR\s+)?Side\s+\d+\]\n|\Z)",
        material,
        flags=re.DOTALL,
    )
    if not page_blocks:
        return material[:max_chars]

    used_pages = set()
    guide_tokens = set()
    stop_words = {
        "hvad", "hvor", "hvordan", "hvilken", "hvornår", "skal", "kan",
        "man", "du", "din", "dit", "dine", "der", "det", "den", "hvis",
        "når", "med", "som", "for", "til", "fra", "eller", "og", "på",
        "er", "en", "et", "har", "have", "bruge", "gøre", "ikke",
    }
    for question in existing_questions:
        try:
            page = int(question.get("source_page", 0))
        except Exception:
            page = 0
        if page:
            used_pages.add(page)

        text = " ".join(
            [
                str(question.get("question", "")),
                str(question.get("correct_answer", "")),
                str(question.get("topic", "")),
            ]
        ).lower()
        for token in re.findall(r"[a-zæøå0-9]+", text):
            if len(token) >= 4 and token not in stop_words:
                guide_tokens.add(token)

    guide_pages: set[int] = set()
    if params:
        guide_examples = get_examples_for_page_range(
            params.age_group,
            params.page_from,
            params.page_to,
        )
        for example in guide_examples:
            try:
                page = int(example.get("source_page", 0))
            except Exception:
                page = 0
            if page:
                guide_pages.add(page)

        for keyword in get_keywords_for_page_range(
            params.age_group,
            params.page_from,
            params.page_to,
        ):
            token = norm_ws(keyword).lower()
            if len(token) >= 4 and token not in stop_words:
                guide_tokens.add(token)

    unsupported_device_terms = {
        "spacer", "spaceren", "hylster", "hylsteret", "plasthylsteret",
        "trykbeholderen", "aerosol", "inhalationssprayen", "sprayen",
        "rengør", "rengøre", "lufttørre", "blød",
    }

    def page_no(block: str) -> int:
        match = re.search(r"Side\s+(\d+)", block)
        return int(match.group(1)) if match else 0

    def is_allowed_block(block: str) -> bool:
        if not params:
            return True
        text = block.lower()
        overlap = any(token in text for token in guide_tokens)
        guided_page = page_no(block) in guide_pages
        if guide_pages and not overlap and not guided_page:
            return False
        block_unsupported_terms = unsupported_device_terms & set(re.findall(r"[a-zæøå]+", text))
        if block_unsupported_terms and not (block_unsupported_terms & guide_tokens):
            return False
        return True

    def block_score(block: str) -> float:
        text = block.lower()
        score = sum(1 for token in guide_tokens if token in text)
        if page_no(block) in guide_pages:
            score += 2.0
        elif page_no(block) not in used_pages:
            score += 0.25
        return score

    page_blocks = [block for block in page_blocks if is_allowed_block(block)]
    if not page_blocks:
        return ""

    ordered_blocks = sorted(
        page_blocks,
        key=lambda block: (
            -block_score(block),
            1 if page_no(block) in used_pages else 0,
            page_no(block),
        ),
    )

    parts: List[str] = []
    used = 0
    for block in ordered_blocks:
        block = norm_ws(block)
        if not block:
            continue
        snippet = block[:320]
        if used + len(snippet) + 2 > max_chars:
            remaining = max_chars - used - 2
            if remaining > 150:
                parts.append(snippet[:remaining])
            break
        parts.append(snippet)
        used += len(snippet) + 2

    return "\n\n".join(parts)[:max_chars]


def supplement_fact_candidates(
    material: str,
    existing_questions: List[Dict[str, Any]],
    params: QuizGenParams,
    max_facts: int = 12,
) -> str:
    age_group = params.age_group
    guide_examples = get_examples_for_page_range(
        age_group,
        params.page_from,
        params.page_to,
    )
    guide_keywords = get_keywords_for_page_range(
        age_group,
        params.page_from,
        params.page_to,
    )
    used_text = " ".join(
        " ".join(
            [
                str(q.get("question", "")),
                str(q.get("correct_answer", "")),
                str(q.get("topic", "")),
            ]
        )
        for q in existing_questions
    ).lower()
    def content_tokens(text: str) -> set[str]:
        tokens = set()
        for token in re.findall(r"[a-zæøå0-9]+", text.lower()):
            if len(token) < 4:
                continue
            tokens.add(token)
            if token.endswith(("e", "r")) and len(token) > 5:
                tokens.add(token[:-1])
        return tokens

    used_tokens = content_tokens(used_text)
    guide_text = " ".join(
        [
            *guide_keywords,
            *[
                " ".join(
                    [
                        str(example.get("question", "")),
                        str(example.get("correct_answer", "")),
                        " ".join(
                            str(option)
                            for option in example.get("options", [])
                            if isinstance(example.get("options", []), list)
                        ),
                    ]
                )
                for example in guide_examples
            ],
        ]
    )
    guide_tokens = content_tokens(guide_text)
    guide_pages = {
        int(example.get("source_page", 0))
        for example in guide_examples
        if str(example.get("source_page", "")).isdigit()
    }
    min_supplement_page = 0
    if (
        params.page_from
        and params.page_to
        and params.page_from >= 15
        and params.page_to - params.page_from >= 12
        and len(guide_examples) >= 5
    ):
        min_supplement_page = params.page_from + 4
    hard_used_answers = [
        norm_ws(str(q.get("correct_answer", ""))).lower()
        for q in existing_questions
        if norm_ws(str(q.get("correct_answer", "")))
    ]

    blocks = re.findall(
        r"\[(?:OCR\s+)?Side\s+(\d+)\]\n(.*?)(?=\n\n\[(?:OCR\s+)?Side\s+\d+\]\n|\Z)",
        material,
        flags=re.DOTALL,
    )
    restricted = (
        ["alveol", "alveolitis", "kapillær", "lungekapill"]
        if age_group.upper() in {"A", "B"}
        else ["alveolitis"]
    )
    unsupported_device_terms = {
        "spacer", "spaceren", "hylster", "hylsteret", "plasthylsteret",
        "trykbeholderen", "aerosol", "inhalationssprayen", "sprayen",
        "rengør", "rengøre", "lufttørre", "blød",
    }
    scored_facts: List[tuple[float, str]] = []
    seen = set()

    for page, text in blocks:
        try:
            source_page = int(page)
        except Exception:
            source_page = 0
        if min_supplement_page and source_page and source_page < min_supplement_page:
            continue
        normalized = norm_ws(text)
        heading = ""
        for raw_line in text.splitlines():
            clean_line = norm_ws(raw_line)
            if clean_line and not clean_line.startswith("•") and not re.match(r"^\d+\.", clean_line):
                heading = clean_line
                break
        candidates = []
        candidates.extend(re.findall(r"•\s*([^•]+?)(?=\s*•|\Z)", normalized))
        candidates.extend(re.findall(r"\d+\.\s*(.*?)(?=\s*\d+\.|\Z)", normalized))
        short_items = [
            norm_ws(item).strip(" .")
            for item in candidates
            if 3 <= len(norm_ws(item).strip(" .")) < 35
        ]
        if (
            heading
            and 2 <= len(short_items) <= 5
            and not re.search(r"\b(?:tegn|symptom|symptomer)\b", heading, flags=re.IGNORECASE)
            and any(_content_word_stems(item) for item in short_items)
        ):
            candidates.append(f"{heading}: {', '.join(short_items)}")

        for candidate in candidates:
            fact = norm_ws(candidate)
            fact = re.split(
                r"\b(?:Grønt område|Gult område|Rødt område|Svarer du)\b",
                fact,
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0].strip(" .")
            fact = re.sub(r"\bpga\.", "pga", fact, flags=re.IGNORECASE)
            sentence_match = re.match(r"^(.+?(?:\.|!|\?))(?:\s|$)", fact)
            if sentence_match and len(sentence_match.group(1)) >= 20:
                fact = sentence_match.group(1).strip()
            if len(fact) < 12:
                continue
            if re.search(r"\bpga$", fact, flags=re.IGNORECASE):
                continue
            if len(fact) > 180:
                continue
            fact_lower = fact.lower()
            if any(term in fact_lower for term in restricted):
                continue
            if any(answer and answer in fact_lower for answer in hard_used_answers):
                continue

            fact_tokens = content_tokens(fact_lower)
            guide_overlap = len(fact_tokens & guide_tokens) if guide_tokens else 0
            page_is_guided = bool(source_page and source_page in guide_pages)
            if guide_examples and not guide_overlap and not page_is_guided:
                continue
            fact_unsupported_terms = unsupported_device_terms & set(re.findall(r"[a-zæøå]+", fact_lower))
            if (
                fact_unsupported_terms
                and not (fact_unsupported_terms & guide_tokens)
            ):
                continue
            if fact_tokens and len(fact_tokens & used_tokens) / max(1, len(fact_tokens)) >= 0.45:
                continue

            key = re.sub(r"[^a-zæøå0-9 ]", "", fact_lower)
            if key in seen:
                continue
            seen.add(key)
            score = float(guide_overlap)
            if page_is_guided:
                score += 1.5
            if re.search(
                r"\b(?:skriv|skrive|vask|vaske|sæt|sætte|åbn|åbne|aflæs|aflæse|ryst|ryste|pust|puste|mål|måle|stop|stoppe|kontakt|kontakte)\b",
                fact_lower,
            ):
                score += 2.0
            if re.search(r"\d+\s*(?:gang|gange|%|procent|uge|uger|minut|minutter)", fact_lower):
                score += 1.0
            if _looks_like_symptom_option(fact_lower) and len(fact_tokens) <= 4:
                score -= 3.0
            if (
                "symptom" in guide_tokens
                and _looks_like_symptom_option(fact_lower)
                and source_page in guide_pages
            ):
                score -= 1.5
            if unsupported_device_terms & set(re.findall(r"[a-zæøå]+", fact_lower)):
                score -= 2.0
            scored_facts.append((score, f"Side {page}: {fact}"))

    scored_facts.sort(key=lambda item: item[0], reverse=True)
    return "\n".join(f"- {fact}" for _score, fact in scored_facts[:max_facts])


def focus_points_for_supplement(fact_material: str, compact_material: str, limit: int = 5) -> List[str]:
    points: List[str] = []
    for line in (fact_material or "").splitlines():
        clean = norm_ws(re.sub(r"^\s*[-•]\s*", "", line))
        if clean:
            points.append(clean)

    if not points:
        for block in re.split(r"\n{2,}", compact_material or ""):
            clean = norm_ws(block)
            if clean:
                points.append(clean[:320])

    seen = set()
    unique: List[str] = []
    for point in points:
        key = re.sub(r"[^a-zæøå0-9 ]", "", point.lower())
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(point)
        if len(unique) >= limit:
            break
    return unique


def template_question_from_pdf_point(point: str, params: QuizGenParams) -> Dict[str, Any] | None:
    point = norm_ws(point)
    page_match = re.search(r"\bSide\s+(\d+)\s*:\s*(.+)$", point, flags=re.IGNORECASE)
    if not page_match:
        return None

    source_page = int(page_match.group(1))
    fact = norm_ws(page_match.group(2))
    fact_lower = fact.lower()
    base = {
        "id": 1,
        "type": "mcq",
        "source_page": source_page,
        "source_fact_id": source_page,
        "difficulty": "easy",
        "origin": "generated",
        "list_group": "",
    }

    wash_match = re.search(
        r"\bvaske\s+dit\s+peak\s*flowmeter\b.*?\bi\s+([^\.]+?opvaskevand)\b",
        fact,
        flags=re.IGNORECASE,
    )
    if wash_match:
        correct = norm_ws(wash_match.group(1)).lower()
        return {
            **base,
            "question": "Hvad skal du vaske dit peakflowmeter i?",
            "options": [
                correct[:1].upper() + correct[1:],
                "Koldt opvaskevand",
                "Varmt vand uden opvaskemiddel",
            ],
            "answer_index": 0,
            "correct_answer": correct[:1].upper() + correct[1:],
            "explanation": f"Du skal vaske dit peakflowmeter i {correct}.",
            "topic": "Vask af peakflowmeter",
        }

    if "tager ekstra medicin" in fact_lower and "peak flowskema" in fact_lower:
        correct = "Skrive det på dit peak flowskema"
        return {
            **base,
            "question": "Hvad skal du gøre, hvis du tager ekstra medicin?",
            "options": [
                correct,
                "Skrive det på en tilfældig seddel",
                "Prøve bare at huske det",
            ],
            "answer_index": 0,
            "correct_answer": correct,
            "explanation": "Du skal skrive det på dit peak flowskema, så du kan holde øje med din astma.",
            "topic": "Medicin og peak flowskema",
        }

    if "mål dit peak flow" in fact_lower and all(word in fact_lower for word in ["før aktivitet", "under aktivitet", "efter aktivitet"]):
        correct = "Før aktivitet, under aktivitet og efter aktivitet"
        return {
            **base,
            "question": "Hvornår skal du måle dit Peak Flow?",
            "options": [
                correct[:1].upper() + correct[1:],
                "Kun før aktivitet",
                "Kun efter aktivitet",
            ],
            "answer_index": 0,
            "correct_answer": correct[:1].upper() + correct[1:],
            "explanation": "Du skal måle dit Peak Flow før aktivitet, under aktivitet og efter aktivitet.",
            "topic": "Måling af Peak Flow",
        }

    return None


def template_supplement_questions(
    material: str,
    params: QuizGenParams,
    current_obj: Dict[str, Any],
    example_obj: Dict[str, Any],
) -> Dict[str, Any]:
    still_missing = params.num_questions - usable_question_count(current_obj)
    if still_missing <= 0:
        return current_obj

    fact_material = supplement_fact_candidates(
        material,
        current_obj.get("questions", []) if isinstance(current_obj.get("questions"), list) else [],
        params,
        max_facts=12,
    )
    candidates: List[Dict[str, Any]] = []
    for point in focus_points_for_supplement(fact_material, fact_material, limit=12):
        question = template_question_from_pdf_point(point, params)
        if question:
            candidates.append(question)

    if not candidates:
        return current_obj

    template_obj = normalize_quiz_obj({"quiz_title": "Spørgsmålsforslag", "questions": candidates})
    template_obj = filter_valid_generated_questions(
        template_obj,
        params,
        example_obj,
        source_material="Ubrugte fakta fra PDF'en:\n" + fact_material,
    )
    if usable_question_count(template_obj) <= 0:
        return current_obj

    return merge_question_sets(current_obj, template_obj, params.num_questions)


def supplement_direct_questions(
    material: str,
    params: QuizGenParams,
    obj: Dict[str, Any],
    missing_count: int,
) -> Optional[Dict[str, Any]]:
    if missing_count <= 0:
        return trim_quiz_to_count(obj, params.num_questions)

    example_obj = examples_to_quiz_obj(params)
    current_obj = obj
    supplement_notes: List[str] = []
    rejection_notes: List[str] = []
    rejected_questions: List[Dict[str, Any]] = []

    attempts = min(max(missing_count + 2, 2), 7)
    used_focus_points: set[str] = set()
    for _attempt in range(attempts):
        if usable_question_count(current_obj) >= params.num_questions:
            break

        still_missing = params.num_questions - usable_question_count(current_obj)
        batch_count = 1
        prompt_questions = [
            *(current_obj.get("questions", []) if isinstance(current_obj.get("questions"), list) else []),
            *rejected_questions,
        ]
        compact_material = compact_material_for_supplement(
            material,
            prompt_questions,
            params=params,
            max_chars=1000,
        )
        fact_material = supplement_fact_candidates(
            material,
            prompt_questions,
            params,
            max_facts=8,
        )
        if fact_material:
            compact_material = "Ubrugte fakta fra PDF'en, som du skal lave nye spørgsmål ud fra:\n" + fact_material
        if rejection_notes:
            compact_material += (
                "\n\nTidligere afviste forsøg, som du ikke må gentage:\n"
                + "\n".join(f"- {note}" for note in rejection_notes[-4:])
            )

        focus_points = focus_points_for_supplement(fact_material, compact_material, limit=8)
        selected_focus_points = [
            point for point in focus_points
            if re.sub(r"[^a-zæøå0-9 ]", "", point.lower()) not in used_focus_points
        ][: max(batch_count + 1, 3)]
        if not selected_focus_points:
            selected_focus_points = focus_points[: max(batch_count + 1, 3)]
        for point in selected_focus_points:
            point_key = re.sub(r"[^a-zæøå0-9 ]", "", point.lower())
            if point_key:
                used_focus_points.add(point_key)

        prompt_text = build_guided_single_supplement_prompt(
            focus_point="\n".join(f"- {point}" for point in selected_focus_points),
            params=params,
            existing_questions=prompt_questions,
            rejection_notes=rejection_notes[-4:],
            question_count=batch_count,
        )

        try:
            supplement_output = ollama_chat(
                model=params.model,
                messages=[
                    {"role": "system", "content": "Returnér kun gyldig JSON. Skriv alt på dansk."},
                    {"role": "user", "content": prompt_text},
                ],
                temperature=0.0,
                timeout=min(300, params.timeout),
                num_ctx=min(900, params.num_ctx),
                num_predict=min(420, params.num_predict),
            )
        except RuntimeError as e:
            supplement_notes.append(str(e))
            rejection_notes.append(
                "Timeout ved PDF-punkter: "
                + "; ".join(point[:80] for point in selected_focus_points[:3])
            )
            break

        supplement_obj = quiz_obj_from_output(supplement_output)
        if not supplement_obj:
            supplement_notes.append(f"Qwen svarede, men JSON kunne ikke parses: {norm_ws(supplement_output)[:220]}")
            continue

        raw_supplement_obj = supplement_obj
        before_filter_count = usable_question_count(raw_supplement_obj)
        supplement_obj = filter_valid_generated_questions(
            raw_supplement_obj,
            params,
            example_obj,
            source_material=compact_material,
        )
        if usable_question_count(supplement_obj) == 0:
            rejection_detail = describe_generated_question_rejections(
                raw_supplement_obj,
                params,
                example_obj,
                source_material=compact_material,
            )
            raw_questions = raw_supplement_obj.get("questions", []) if isinstance(raw_supplement_obj, dict) else []
            if isinstance(raw_questions, list):
                rejected_questions.extend(
                    q for q in raw_questions
                    if isinstance(q, dict)
                )
            rejection_notes.append(rejection_detail)
            supplement_notes.append(
                f"Qwen lavede {before_filter_count} forslag, men alle blev afvist af valideringen. {rejection_detail}"
            )
            continue

        merged = merge_question_sets(current_obj, supplement_obj, params.num_questions)
        if usable_question_count(merged) > usable_question_count(current_obj):
            current_obj = merged
            supplement_notes.append(
                f"Qwen lavede {usable_question_count(supplement_obj)} brugbare ekstra spørgsmål."
            )
        else:
            supplement_notes.append(
                f"Qwen lavede {usable_question_count(supplement_obj)} forslag, men de lignede eksisterende spørgsmål for meget."
            )

    if supplement_notes:
        current_obj["_supplement_note"] = " ".join(supplement_notes)
    if usable_question_count(current_obj) < params.num_questions:
        before_template_count = usable_question_count(current_obj)
        current_obj = template_supplement_questions(material, params, current_obj, example_obj)
        added_template_count = usable_question_count(current_obj) - before_template_count
        if added_template_count > 0:
            current_obj["_supplement_note"] = (
                (current_obj.get("_supplement_note", "") + " ").strip()
                + f"Tilføjede {added_template_count} ekstra spørgsmål fra tydelige PDF-punkter."
            ).strip()
    if usable_question_count(current_obj) > 0:
        return current_obj

    return None


def generate_quiz_candidates_direct(
    material: str,
    used_ocr: bool,
    params: QuizGenParams,
    n_candidates: int,
) -> Tuple[Dict[str, Any], QuizGenDebug]:
    example_obj = examples_to_quiz_obj(params)
    example_err = validate_quiz_schema(example_obj) if usable_question_count(example_obj) else None
    if example_err:
        example_obj = {"quiz_title": "Spørgsmålsforslag", "questions": []}

    if usable_question_count(example_obj) >= params.num_questions:
        obj = trim_quiz_to_count(example_obj, params.num_questions)
        page_range = f"{params.page_from or 1}-{params.page_to or 'sidste'}"
        debug = QuizGenDebug(
            extracted_chars=len(material),
            used_ocr=used_ocr,
            material_preview=material[:4000],
            model_latency_s=0.0,
            did_repair=False,
            raw_model_output="Brugte relevante eksempelspørgsmål fra valgt hold og sideinterval.",
            page_range=page_range,
            facts_count=0,
            facts_preview="",
        )
        return obj, debug

    start_time = time.time()
    if usable_question_count(example_obj) > 0:
        missing_count = params.num_questions - usable_question_count(example_obj)
        supplemented_obj = supplement_direct_questions(material, params, example_obj, missing_count)
        latency = time.time() - start_time
        if supplemented_obj and usable_question_count(supplemented_obj) > 0:
            obj = trim_quiz_to_count(supplemented_obj, params.num_questions)
            page_range = f"{params.page_from or 1}-{params.page_to or 'sidste'}"
            complete = usable_question_count(obj) >= params.num_questions
            supplement_note = supplemented_obj.get("_supplement_note", "")
            debug = QuizGenDebug(
                extracted_chars=len(material),
                used_ocr=used_ocr,
                material_preview=material[:4000],
                model_latency_s=latency,
                did_repair=False,
                raw_model_output=(
                    "Brugte relevante eksempelspørgsmål og genererede kun de manglende spørgsmål."
                    if complete
                    else "Brugte delvise forslag. Ekstra-generering nåede ikke at lave alle ønskede spørgsmål."
                ),
                page_range=page_range,
                facts_count=0,
                facts_preview="",
            )
            if supplement_note:
                debug.raw_model_output += f" Detalje: {supplement_note}"
            return obj, debug

        obj = trim_quiz_to_count(example_obj, params.num_questions)
        page_range = f"{params.page_from or 1}-{params.page_to or 'sidste'}"
        debug = QuizGenDebug(
            extracted_chars=len(material),
            used_ocr=used_ocr,
            material_preview=material[:4000],
            model_latency_s=latency,
            did_repair=False,
            raw_model_output="Brugte kun eksempelspørgsmål. Ekstra-generering nåede ikke at lave brugbare spørgsmål.",
            page_range=page_range,
            facts_count=0,
            facts_preview="",
        )
        return obj, debug

    requested_generated = max(params.num_questions - usable_question_count(example_obj), 1)
    generated_target = candidate_count(requested_generated)
    token_budget = min(max(params.num_predict, generated_target * 230), 2200)

    model_output = ollama_chat(
        model=params.model,
        messages=[
            {"role": "system", "content": "Svar kun med gyldig JSON. Skriv alt på dansk."},
            {
                "role": "user",
                "content": build_direct_question_generation_prompt(
                    material=material,
                    params=params,
                    n_candidates=generated_target,
                ),
            },
        ],
        temperature=params.temperature,
        timeout=min(300, params.timeout),
        num_ctx=params.num_ctx,
        num_predict=token_budget,
    )
    latency = time.time() - start_time

    raw_output_final = model_output
    did_repair = False

    obj = quiz_obj_from_output(model_output)
    if obj:
        obj = normalize_quiz_obj(obj)
        obj = shuffle_questions(obj)

    err = validate_generated_quiz(obj, params)
    if err:
        filtered_obj = filter_valid_generated_questions(obj, params, example_obj)
        if usable_question_count(filtered_obj) > 0:
            obj = filtered_obj
            err = None

    if err:
        did_repair = True
        repaired_output = ollama_chat(
            model=params.model,
            messages=[
                {"role": "system", "content": "Returnér kun gyldig JSON. Skriv alt på dansk."},
                {
                    "role": "user",
                    "content": build_direct_repair_prompt(
                        quiz_obj=obj,
                        raw_output=model_output,
                        material=material,
                        params=params,
                        expected_count=generated_target,
                    ),
                },
            ],
            temperature=0.0,
            timeout=min(300, params.timeout),
            num_ctx=params.num_ctx,
            num_predict=token_budget,
        )
        raw_output_final = repaired_output

        obj = quiz_obj_from_output(repaired_output)
        if obj:
            obj = normalize_quiz_obj(obj)
            obj = shuffle_questions(obj)

        err = validate_generated_quiz(obj, params)
        if err:
            filtered_obj = filter_valid_generated_questions(obj, params, example_obj)
            if usable_question_count(filtered_obj) > 0:
                obj = filtered_obj
                err = None
        if err:
            if usable_question_count(example_obj) > 0:
                obj = {"quiz_title": "Spørgsmålsforslag", "questions": []}
                err = None
            else:
                repair_err = err
                obj = {"quiz_title": "Spørgsmålsforslag", "questions": []}
                err = None
                raw_output_final = (
                    f"Første generering/repair gav ingen brugbare spørgsmål ({repair_err}). "
                    "Prøver ekstra-generering ud fra PDF-punkter."
                )

    if usable_question_count(example_obj) > 0:
        obj = merge_question_sets(example_obj, obj, params.num_questions)

    if usable_question_count(obj) < params.num_questions:
        missing_count = params.num_questions - usable_question_count(obj)
        supplemented_obj = supplement_direct_questions(material, params, obj, missing_count)
        if supplemented_obj:
            obj = supplemented_obj
            supplement_note = supplemented_obj.get("_supplement_note", "")
            if supplement_note:
                raw_output_final += f" Detalje: {supplement_note}"

    ensure_enough_questions(obj, params.num_questions)
    obj = trim_quiz_to_count(obj, params.num_questions)

    page_range = f"{params.page_from or 1}-{params.page_to or 'sidste'}"
    debug = QuizGenDebug(
        extracted_chars=len(material),
        used_ocr=used_ocr,
        material_preview=material[:4000],
        model_latency_s=latency,
        did_repair=did_repair,
        raw_model_output=raw_output_final,
        page_range=page_range,
        facts_count=0,
        facts_preview="",
    )

    return obj, debug


def generate_quiz_candidates_from_pdf(
    pdf_bytes: bytes,
    params: QuizGenParams,
) -> Tuple[Dict[str, Any], QuizGenDebug]:
    try:
        requests.get(OLLAMA_TAGS_URL, timeout=3).raise_for_status()
    except Exception as e:
        raise RuntimeError(f"Kan ikke nå Ollama på {OLLAMA_HOST}. Fejl: {e}")

    material, used_ocr = extract_material_from_pdf(pdf_bytes, params)
    n_candidates = candidate_count(params.num_questions)

    if not params.use_fact_pipeline:
        return generate_quiz_candidates_direct(
            material=material,
            used_ocr=used_ocr,
            params=params,
            n_candidates=n_candidates,
        )

    max_facts = min(max(params.num_questions + 3, 8), 12)
    generation_token_budget = min(params.num_predict, max(900, n_candidates * 170))

    facts_obj = extract_facts_from_material(material, params, max_facts=max_facts)
    facts = facts_obj.get("facts", [])

    start_time = time.time()
    model_output = ollama_chat(
        model=params.model,
        messages=[
            {"role": "system", "content": "Svar kun med gyldig JSON. Skriv alt på dansk."},
            {
                "role": "user",
                "content": build_question_generation_prompt(
                    material=material,
                    facts_obj=facts_obj,
                    params=params,
                    n_candidates=n_candidates,
                ),
            },
        ],
        temperature=params.temperature,
        timeout=params.timeout,
        num_ctx=params.num_ctx,
        num_predict=generation_token_budget,
    )
    latency = time.time() - start_time

    did_repair = False
    raw_output_final = model_output

    obj = quiz_obj_from_output(model_output)
    if obj:
        obj = normalize_quiz_obj(obj)
        obj = shuffle_questions(obj)

    err = validate_quiz_schema(obj) if obj else "Kunne ikke parse JSON."
    if not err and obj:
        err = validate_mcq_quality(obj)
    if not err and obj:
        err = validate_answer_sources(obj, facts_obj)
    if not err and obj:
        err = validate_danish_language_quality(obj)

    if err:
        did_repair = True

        repaired_output = ollama_chat(
            model=params.model,
            messages=[
                {"role": "system", "content": "Returnér kun gyldig JSON. Skriv alt på dansk."},
                {
                    "role": "user",
                    "content": build_review_prompt(
                        quiz_obj=obj if obj else {"quiz_title": "Spørgsmålsforslag", "questions": []},
                        material=material,
                        params=params,
                        expected_count=n_candidates,
                        facts_obj=facts_obj,
                    ),
                },
            ],
            temperature=0.0,
            timeout=params.timeout,
            num_ctx=params.num_ctx,
            num_predict=min(1200, max(900, n_candidates * 160)),
        )

        raw_output_final = repaired_output

        obj2 = quiz_obj_from_output(repaired_output)
        if obj2:
            obj2 = normalize_quiz_obj(obj2)
            obj2 = shuffle_questions(obj2)

        err2 = validate_quiz_schema(obj2) if obj2 else "Kunne ikke parse repareret JSON."
        if not err2 and obj2:
            err2 = validate_mcq_quality(obj2)
        if not err2 and obj2:
            err2 = validate_answer_sources(obj2, facts_obj)
        if not err2 and obj2:
            err2 = validate_danish_language_quality(obj2)

        if err2:
            regenerated_output = ollama_chat(
                model=params.model,
                messages=[
                    {"role": "system", "content": "Returnér kun gyldig JSON. Skriv alt på dansk."},
                    {
                        "role": "user",
                        "content": build_question_generation_prompt(
                            material=material,
                            facts_obj=facts_obj,
                            params=params,
                            n_candidates=n_candidates,
                        ),
                    },
                ],
                temperature=0.1,
                timeout=min(300, params.timeout),
                num_ctx=params.num_ctx,
                num_predict=min(1400, max(900, n_candidates * 170)),
            )

            raw_output_final = regenerated_output

            obj3 = quiz_obj_from_output(regenerated_output)
            if obj3:
                obj3 = normalize_quiz_obj(obj3)
                obj3 = shuffle_questions(obj3)

            err3 = validate_quiz_schema(obj3) if obj3 else "Kunne ikke parse regenereret JSON."
            if not err3 and obj3:
                err3 = validate_mcq_quality(obj3)
            if not err3 and obj3:
                err3 = validate_answer_sources(obj3, facts_obj)
            if not err3 and obj3:
                err3 = validate_danish_language_quality(obj3)

            if not err3 and obj3:
                try:
                    ensure_enough_questions(obj3, params.num_questions)
                except RuntimeError as e:
                    err3 = str(e)
                
            if err3:
                raise RuntimeError(f"Spørgsmålsforslag er stadig ugyldige efter repair: {err3}")

            obj = obj3
        else:
            obj = obj2

    if params.run_final_review:
        reviewed_obj = run_review_step(obj, material, params, params.num_questions, facts_obj=facts_obj)
        if reviewed_obj:
            review_err = validate_quiz_schema(reviewed_obj)
            if not review_err:
                review_err = validate_mcq_quality(reviewed_obj)
            if not review_err:
                review_err = validate_answer_sources(reviewed_obj, facts_obj)
            if not review_err:
                review_err = validate_danish_language_quality(reviewed_obj)
            if not review_err:
                obj = reviewed_obj

    ensure_enough_questions(obj, params.num_questions)

    page_range = f"{params.page_from or 1}-{params.page_to or 'sidste'}"
    facts_preview = json.dumps(facts[:8], ensure_ascii=False, indent=2)

    debug = QuizGenDebug(
        extracted_chars=len(material),
        used_ocr=used_ocr,
        material_preview=material[:4000],
        model_latency_s=latency,
        did_repair=did_repair,
        raw_model_output=raw_output_final,
        page_range=page_range,
        facts_count=len(facts),
        facts_preview=facts_preview,
    )

    return obj, debug


def regenerate_single_question_from_pdf(
    pdf_bytes: bytes,
    params: QuizGenParams,
    old_question: Dict[str, Any],
    existing_questions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    try:
        requests.get(OLLAMA_TAGS_URL, timeout=3).raise_for_status()
    except Exception as e:
        raise RuntimeError(f"Kan ikke nå Ollama på {OLLAMA_HOST}. Fejl: {e}")

    material, _used_ocr = extract_material_from_pdf(pdf_bytes, params)
    facts_obj = extract_facts_from_material(material, params, max_facts=14)

    prompt = build_single_regenerate_prompt(
        material=material,
        params=params,
        old_question=old_question,
        existing_questions=existing_questions,
        facts_obj=facts_obj,
    )

    output = ollama_chat(
        model=params.model,
        messages=[
            {"role": "system", "content": "Returnér kun gyldig JSON. Skriv alt på dansk."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        timeout=params.timeout,
        num_ctx=params.num_ctx,
        num_predict=1200,
    )

    obj = quiz_obj_from_output(output)
    if not obj:
        raise RuntimeError("Kunne ikke parse JSON for nyt spørgsmål.")

    obj = normalize_quiz_obj(obj)

    err = validate_quiz_schema(obj, expected_count=1)
    if err:
        raise RuntimeError(f"Nyt spørgsmål er ugyldigt: {err}")

    err2 = validate_mcq_quality(obj)
    if err2:
        raise RuntimeError(f"Nyt spørgsmål har kvalitetsfejl: {err2}")
    err3 = validate_answer_sources(obj, facts_obj)
    if err3:
        raise RuntimeError(f"Nyt spørgsmål bruger ikke svar korrekt fra materialet: {err3}")
    err4 = validate_danish_language_quality(obj)
    if err4:
        raise RuntimeError(f"Nyt spørgsmål har sproglige kvalitetsfejl: {err4}")

    q = obj["questions"][0]

    old_sig = question_signature(old_question)
    new_sig = question_signature(q)
    existing_sigs = {question_signature(item) for item in existing_questions}

    if new_sig == old_sig:
        raise RuntimeError("Det nye spørgsmål ligner for meget det gamle spørgsmål.")

    if new_sig in existing_sigs:
        raise RuntimeError("Det nye spørgsmål ligner for meget et eksisterende spørgsmål.")

    old_fact = old_question.get("source_fact_id")
    new_fact = q.get("source_fact_id")
    if old_fact == new_fact:
        old_topic = norm_ws(str(old_question.get("topic", ""))).lower()
        new_topic = norm_ws(str(q.get("topic", ""))).lower()
        if old_topic == new_topic:
            raise RuntimeError("Det nye spørgsmål bygger sandsynligvis på samme pointe som det gamle.")

    return q


def finalize_selected_questions(
    selected_questions: List[Dict[str, Any]],
    quiz_title: str = "Valgt quiz",
) -> Dict[str, Any]:
    if not selected_questions:
        raise ValueError("Ingen valgte spørgsmål")

    quiz_obj = {
        "quiz_title": quiz_title,
        "questions": renumber_questions(selected_questions),
    }

    quiz_obj = normalize_quiz_obj(quiz_obj)

    err = validate_quiz_schema(quiz_obj)
    if err:
        raise ValueError(f"Valgte spørgsmål er ugyldige: {err}")

    err2 = validate_mcq_quality(quiz_obj)
    if err2:
        raise ValueError(f"Valgte spørgsmål har kvalitetsfejl: {err2}")

    err3 = validate_danish_language_quality(quiz_obj)
    if err3:
        raise ValueError(f"Valgte spørgsmål har sproglige kvalitetsfejl: {err3}")

    return quiz_obj
