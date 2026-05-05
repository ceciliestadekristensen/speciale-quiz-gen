# app/services/quiz_validation.py

import re
from typing import Any


def norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def question_signature(question: dict[str, Any]) -> str:
    q = norm_ws(str(question.get("question", ""))).lower()
    q = re.sub(r"[^a-zæøå0-9 ]", "", q)
    return q


def semantic_question_signature(question: dict[str, Any]) -> str:
    text = norm_ws(str(question.get("question", ""))).lower()
    text = re.sub(r"peak\s*flow", "peakflow", text)
    text = re.sub(r"[^a-zæøå0-9 ]", " ", text)
    stop_words = {
        "hvad", "hvornår", "hvordan", "hvilken", "hvilket", "kan", "skal",
        "du", "man", "dit", "din", "dine", "der", "hvis", "når", "om",
        "og", "at", "for", "til", "i", "på", "med", "er",
    }
    tokens = [token for token in text.split() if token not in stop_words and len(token) >= 3]
    return " ".join(sorted(set(tokens)))


def answer_signature(question: dict[str, Any]) -> str:
    answer = norm_ws(str(question.get("correct_answer", ""))).lower()
    answer = re.sub(r"peak\s*flow", "peakflow", answer)
    answer = re.sub(r"[^a-zæøå0-9 ]", " ", answer)
    return norm_ws(answer)


def filter_out_similar_questions(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen_question = set()
    seen_semantic_question = set()
    seen_topic_fact = set()
    seen_generated_answer = set()
    unique = []

    for q in questions:
        sig = question_signature(q)
        semantic_sig = semantic_question_signature(q)
        topic = norm_ws(str(q.get("topic", ""))).lower()
        fact_id = str(q.get("source_fact_id", ""))
        topic_fact = f"{topic}:{fact_id}"
        origin = str(q.get("origin", "generated")).lower()
        try:
            source_page = int(q.get("source_page", 0))
        except Exception:
            source_page = 0
        answer_key = f"{source_page}:{answer_signature(q)}"

        if not sig:
            continue
        if sig in seen_question:
            continue
        if semantic_sig and semantic_sig in seen_semantic_question:
            continue
        if topic_fact != ":" and topic_fact in seen_topic_fact:
            continue
        if origin != "example" and answer_key != f"{source_page}:" and answer_key in seen_generated_answer:
            continue

        seen_question.add(sig)
        if semantic_sig:
            seen_semantic_question.add(semantic_sig)
        seen_topic_fact.add(topic_fact)
        if origin != "example" and answer_key != f"{source_page}:":
            seen_generated_answer.add(answer_key)
        unique.append(q)

    return unique


def validate_quiz_schema(obj: dict[str, Any], expected_count: int | None = None) -> str | None:
    if not isinstance(obj, dict):
        return "JSON er ikke et objekt."

    questions = obj.get("questions")
    if not isinstance(questions, list) or not questions:
        return "JSON mangler 'questions' eller listen er tom."

    if expected_count is not None and len(questions) != expected_count:
        return f"Der skal være præcis {expected_count} spørgsmål."

    for q in questions:
        if not isinstance(q, dict):
            return "Et spørgsmål er ikke et objekt."

        required = [
            "id", "type", "question", "options", "answer_index",
            "explanation", "source_page", "source_fact_id", "topic", "difficulty",
            "correct_answer", "list_group",
        ]
        for key in required:
            if key not in q:
                return f"Spørgsmål mangler feltet '{key}'."

        if q.get("type") != "mcq":
            return "Kun 'mcq' er tilladt."

        if len(norm_ws(str(q["question"]))) < 8:
            return "Et spørgsmål er for kort."

        if not isinstance(q["options"], list) or len(q["options"]) != 3:
            return "MCQ skal have præcis 3 svarmuligheder."

        if q["answer_index"] not in [0, 1, 2]:
            return "answer_index skal være 0, 1 eller 2."

        option_norms = [norm_ws(str(o)).lower() for o in q["options"]]
        if len(set(option_norms)) < 3:
            return "Svarmulighederne er for ens."

        if len(norm_ws(str(q["explanation"]))) < 8:
            return "Forklaringen er for kort."

        correct_answer = norm_ws(str(q.get("correct_answer", "")))
        if len(correct_answer) < 2:
            return "correct_answer mangler."

        correct_option = norm_ws(str(q["options"][q["answer_index"]]))
        if correct_option.lower() != correct_answer.lower():
            return "Den korrekte svarmulighed matcher ikke correct_answer."

    return None


def validate_mcq_quality(obj: dict[str, Any]) -> str | None:
    questions = obj.get("questions", [])
    if not isinstance(questions, list):
        return "questions er ikke en liste."

    seen_question_keys = set()

    for q in questions:
        question_text = norm_ws(str(q.get("question", ""))).lower()
        question_key = re.sub(r"[^a-zæøå0-9 ]", "", question_text)

        if question_key in seen_question_keys:
            return "Der er dublerede eller næsten identiske spørgsmål."
        seen_question_keys.add(question_key)

        options = q.get("options", [])
        if not isinstance(options, list) or len(options) != 3:
            return "MCQ skal have præcis 3 svarmuligheder."

        norm_options = [norm_ws(str(o)).lower() for o in options]
        if len(set(norm_options)) < 3:
            return "Svarmulighederne er for ens."
        correct_idx = q.get("answer_index")
        if correct_idx not in [0, 1, 2]:
            return "answer_index skal være 0-2."

        correct_answer = norm_ws(str(q.get("correct_answer", ""))).lower()
        if not correct_answer:
            return "correct_answer mangler."

        if norm_options[correct_idx] != correct_answer:
            return "Det markerede korrekte svar matcher ikke correct_answer."

        banned = ["alle ovenstående", "ingen af ovenstående", "ved ikke"]
        for opt in norm_options:
            if any(b in opt for b in banned):
                return "Uegnede standardsvar fundet."
            if opt.startswith(("giver ", "give ")):
                return "En svarmulighed er unaturligt formuleret."

    return None


def validate_against_example_answers(obj: dict[str, Any], example_obj: dict[str, Any]) -> str | None:
    questions = obj.get("questions", []) if isinstance(obj, dict) else []
    examples = example_obj.get("questions", []) if isinstance(example_obj, dict) else []
    if not isinstance(questions, list) or not isinstance(examples, list):
        return None

    def is_short_count_inside_frequency(option_text: str, known_answer: str) -> bool:
        known = norm_ws(known_answer).lower()
        option = norm_ws(option_text).lower()
        return (
            bool(re.fullmatch(r"\d+\s*gang(?:e)?", known))
            and bool(re.search(r"\d+\s*gang(?:e)?\s+om\s+(?:dag|dagen|uge|ugen|måned|måneden)", option))
        )

    example_answers_by_group: dict[str, set[str]] = {}
    example_answers_by_page: dict[int, set[str]] = {}
    example_questions: list[tuple[str, str]] = []
    for example in examples:
        group = norm_ws(str(example.get("list_group", ""))).lower()
        answer = norm_ws(str(example.get("correct_answer", ""))).lower()
        question = norm_ws(str(example.get("question", ""))).lower()
        if question and answer:
            example_questions.append((question, answer))
        try:
            page = int(example.get("source_page", 0))
        except Exception:
            page = 0
        if group and answer:
            example_answers_by_group.setdefault(group, set()).add(answer)
        if page and answer:
            example_answers_by_page.setdefault(page, set()).add(answer)

    for q in questions:
        group = norm_ws(str(q.get("list_group", ""))).lower()
        correct = norm_ws(str(q.get("correct_answer", ""))).lower()
        question = norm_ws(str(q.get("question", ""))).lower()
        try:
            page = int(q.get("source_page", 0))
        except Exception:
            page = 0
        options = q.get("options", [])
        answer_index = q.get("answer_index")
        if not isinstance(options, list) or answer_index not in [0, 1, 2]:
            continue

        for example_question, example_answer in example_questions:
            if _questions_are_too_similar(question, example_question) and _answers_overlap(correct, example_answer):
                return "Et spørgsmål kopierer eller omformulerer et eksempelspørgsmål for tæt."

        known_answers = set()
        if group:
            known_answers.update(example_answers_by_group.get(group, set()))
        if page:
            known_answers.update(example_answers_by_page.get(page, set()))
        if not known_answers:
            continue

        for idx, option in enumerate(options):
            option_norm = norm_ws(str(option)).lower()
            if idx == answer_index or option_norm == correct:
                continue
            if any(
                _answers_overlap(option_norm, known_answer)
                and not is_short_count_inside_frequency(option_norm, known_answer)
                for known_answer in known_answers
            ):
                return "En forkert svarmulighed er et andet korrekt eksempel-svar fra samme emne."

    return None


def _normalize_answer_text(value: str) -> str:
    value = norm_ws(value).lower()
    value = re.sub(r"^[\-\u2022\*\d\.\)\s]+", "", value)
    value = re.sub(r"[^a-zæøå0-9 ]", "", value)
    stop_words = {
        "en", "et", "den", "det", "de", "der", "som", "kan", "være",
        "er", "at", "du", "man", "skal", "til", "med", "på", "i", "for",
    }
    words = [word for word in value.split() if word not in stop_words]
    return " ".join(words)


def _answer_matches_fact(answer: str, fact: str) -> bool:
    answer_key = _normalize_answer_text(answer)
    fact_key = _normalize_answer_text(fact)
    if not answer_key or not fact_key:
        return False
    return answer_key == fact_key or answer_key in fact_key or fact_key in answer_key


def _content_tokens(value: str) -> set[str]:
    tokens = set()
    for token in _normalize_answer_text(value).split():
        if len(token) < 5:
            continue
        tokens.add(token)
        if token.endswith("r") and len(token) > 6:
            tokens.add(token[:-1])
    return tokens


def _answers_overlap(a: str, b: str) -> bool:
    if _answer_matches_fact(a, b):
        return True
    return bool(_content_tokens(a) & _content_tokens(b))


def _answers_are_same_text(a: str, b: str) -> bool:
    a_key = _normalize_answer_text(a)
    b_key = _normalize_answer_text(b)
    return bool(a_key and b_key and a_key == b_key)


def _questions_are_too_similar(a: str, b: str) -> bool:
    a_key = _normalize_answer_text(a)
    b_key = _normalize_answer_text(b)
    if not a_key or not b_key:
        return False
    if a_key == b_key or a_key in b_key or b_key in a_key:
        return True

    a_tokens = {token for token in a_key.split() if len(token) >= 4}
    b_tokens = {token for token in b_key.split() if len(token) >= 4}
    if len(a_tokens) < 3 or len(b_tokens) < 3:
        return False

    overlap = len(a_tokens & b_tokens) / min(len(a_tokens), len(b_tokens))
    return overlap >= 0.75


def validate_answer_sources(obj: dict[str, Any], facts_obj: dict[str, Any]) -> str | None:
    questions = obj.get("questions", [])
    facts = facts_obj.get("facts", []) if isinstance(facts_obj, dict) else []
    if not isinstance(questions, list) or not isinstance(facts, list):
        return None

    facts_by_id: dict[int, dict[str, Any]] = {}
    fact_answers: list[tuple[int, str, str, str, str]] = []

    for fact in facts:
        if not isinstance(fact, dict):
            continue
        try:
            fact_id = int(fact.get("fact_id"))
        except Exception:
            continue

        topic = norm_ws(str(fact.get("topic", ""))).lower()
        list_group = norm_ws(str(fact.get("list_group", ""))).lower()
        fact_text = norm_ws(str(fact.get("fact", "")))
        correct_answer = norm_ws(str(fact.get("correct_answer", ""))) or fact_text
        facts_by_id[fact_id] = fact
        fact_answers.append((fact_id, topic, list_group, correct_answer, fact_text))

    for q in questions:
        if not isinstance(q, dict):
            continue

        try:
            source_fact_id = int(q.get("source_fact_id"))
        except Exception:
            return "Et spørgsmål har ugyldigt source_fact_id."

        source_fact = facts_by_id.get(source_fact_id)
        if not source_fact:
            return "Et spørgsmål bygger ikke på en kendt fact fra materialet."

        options = q.get("options", [])
        answer_index = q.get("answer_index")
        if not isinstance(options, list) or answer_index not in [0, 1, 2]:
            return "Et spørgsmål har ugyldige svarmuligheder."

        correct_option = norm_ws(str(options[answer_index]))
        source_answer = norm_ws(str(source_fact.get("correct_answer", ""))) or norm_ws(str(source_fact.get("fact", "")))
        source_fact_text = norm_ws(str(source_fact.get("fact", "")))

        if not (
            correct_option.lower() == source_answer.lower()
            or _answer_matches_fact(correct_option, source_answer)
            or _answer_matches_fact(correct_option, source_fact_text)
        ):
            return "Det rigtige svar ser ikke ud til at komme fra PDF-materialet."

        topic = norm_ws(str(q.get("topic", ""))).lower()
        list_group = norm_ws(str(source_fact.get("list_group", ""))).lower()
        wrong_options = [
            norm_ws(str(option))
            for idx, option in enumerate(options)
            if idx != answer_index
        ]

        for wrong_option in wrong_options:
            for fact_id, fact_topic, fact_list_group, fact_answer, fact_text in fact_answers:
                if fact_id == source_fact_id:
                    continue
                same_group = list_group and fact_list_group and list_group == fact_list_group
                same_topic_without_group = not list_group and topic and fact_topic and topic == fact_topic
                if (same_group or same_topic_without_group) and (
                    _answer_matches_fact(wrong_option, fact_answer)
                    or _answer_matches_fact(wrong_option, fact_text)
                ):
                    return "En forkert svarmulighed er et andet korrekt punkt fra samme liste eller emne."

    return None

def validate_danish_language_quality(obj: dict[str, Any]) -> str | None:
    questions = obj.get("questions", [])
    if not isinstance(questions, list):
        return "questions er ikke en liste."

    bad_words = [
        "astmatak",
        "attack",
        "tool",
        "tools",
        "cause",
        "hvad er en mål",
        "hvad er en tegn",
        "hvad er et tegn på at",
        "hvad er en tegn på at",
        "hvad er en daglig rutine for",
        "hvad kan føre til at du skal bruge",
        "hvad kan føre til, at du skal bruge",
        "hvad skal du gøre med din",
        "hvilken af følgende",
        "hvad kan forværre din astma",
        "giver ingen opmærksomhed",
        "forklaring mangler",
        "plads dem",
        "alveolitis",
        "trætter",
        "hylsten",
        "morningen",
        "rejsning",
        "efter spise",
    ]

    english_words = [
        "tool",
        "tools",
        "attack",
        "use",
        "treatment",
        "daily",
        "lunch",
        "routine",
        "symptoms",
        "medicine",
        "doctor",
        "inhaler",
        "cause",
    ]
    suspicious_non_danish_words = [
        "sachet",
        "sachetten",
        "ligesit",
    ]
    awkward_option_phrases = [
        "tage med det",
        "tage med den",
        "lade det liges",
        "lade den liges",
    ]

    for q in questions:
        texts = [
            str(q.get("question", "")),
            str(q.get("explanation", "")),
            *[str(o) for o in q.get("options", [])],
        ]

        combined = " ".join(texts).lower()

        if any(word in combined for word in bad_words):
            return "Et spørgsmål har dårlig formulering eller blandet sprog."

        if str(q.get("origin", "generated")).lower() != "example":
            generated_bad_content = [
                "superkræfter",
                "længere hår",
                "ondt i tåen",
                "ondt i armen",
            ]
            if any(word in combined for word in generated_bad_content):
                return "Et genereret spørgsmål bruger en fjollet eller forkert svarmulighed som fagligt svar."

        if any(word in combined.split() for word in english_words):
            return "Et spørgsmål indeholder engelske ord."

        if str(q.get("origin", "generated")).lower() != "example":
            generated_options = [norm_ws(str(option)).lower() for option in q.get("options", [])]
            if any(
                any(word in option for word in suspicious_non_danish_words)
                for option in generated_options
            ):
                return "En svarmulighed bruger et opfundet eller ikke-dansk ord."
            if any(
                any(phrase in option for phrase in awkward_option_phrases)
                for option in generated_options
            ):
                return "En svarmulighed er unaturligt formuleret."

        question = str(q.get("question", "")).strip()

        if not question.endswith("?"):
            return "Et spørgsmål mangler spørgsmålstegn."

        if len(question.split()) < 4 and not re.match(
            r"^(hvad|hvem|hvor|hvornår|hvordan|hvilk(?:et|en)|må|kan|skal)\b.+\?$",
            question.lower(),
        ):
            return "Et spørgsmål er for kort."

        awkward_starts = [
            "hvad er en mål",
            "hvad er en tegn",
            "hvad er et tegn på at",
            "hvad kan føre til",
            "hvad skal du gøre med",
            "hvad er en daglig rutine",
            "hvad kan forværre",
            "hvad kan du ofte opleve",
        ]

        if any(question.lower().startswith(start) for start in awkward_starts):
            return "Et spørgsmål er unaturligt formuleret."

        awkward_phrases = [
            "tegn på at du har træt",
            "tegn på, at du har træt",
            "tegn på at du har forpustet",
            "tegn på, at du har forpustet",
            "har træt og forpustet",
            "har træt",
            "har forpustet",
            "når du trætter",
            "du trætter",
            "sætte sprayen i hylsten",
            "hoster kan ofte opleves som pibende",
            "hoste kan ofte opleves som pibende",
        ]
        if any(phrase in question.lower() for phrase in awkward_phrases):
            return "Et spørgsmål er unaturligt formuleret."

        if "hvilken af følgende" in question.lower():
            return "Et spørgsmål har en klodset standardformulering."

        exercise_words = {"løb", "løbe", "cykle", "cykling", "idræt", "sport", "motion", "leg"}
        option_words = {
            re.sub(r"[^a-zæøå0-9 ]", "", norm_ws(str(option)).lower())
            for option in q.get("options", [])
        }
        if "forværre" in question.lower() and sum(any(word in option for word in exercise_words) for option in option_words) >= 2:
            return "Et spørgsmål har flere svarmuligheder, der kan være rigtige."

    return None


def validate_age_group_content(obj: dict[str, Any], age_group: str) -> str | None:
    questions = obj.get("questions", []) if isinstance(obj, dict) else []
    if not isinstance(questions, list):
        return "questions er ikke en liste."

    restricted_by_age = {
        "A": ["alveol", "alveolitis"],
        "B": ["alveol", "alveolitis"],
        "C": ["alveolitis"],
    }
    restricted = restricted_by_age.get(age_group.upper(), [])

    for q in questions:
        combined = " ".join(
            [
                str(q.get("question", "")),
                str(q.get("explanation", "")),
                str(q.get("topic", "")),
                *[str(option) for option in q.get("options", [])],
            ]
        ).lower()
        if any(term in combined for term in restricted):
            return "Et spørgsmål bruger et for svært fagord for aldersgruppen."

    return None


def validate_option_set_diversity(obj: dict[str, Any]) -> str | None:
    questions = obj.get("questions", []) if isinstance(obj, dict) else []
    if not isinstance(questions, list):
        return "questions er ikke en liste."

    seen_sets = set()
    for q in questions:
        if str(q.get("origin", "generated")).lower() == "example":
            continue
        options = q.get("options", [])
        if not isinstance(options, list):
            continue
        option_set = tuple(sorted(re.sub(r"[^a-zæøå0-9 ]", "", norm_ws(str(option)).lower()) for option in options))
        if option_set in seen_sets:
            return "Flere spørgsmål har samme svarmuligheder."
        seen_sets.add(option_set)

    return None


def validate_source_pages(obj: dict[str, Any], page_from: int | None, page_to: int | None) -> str | None:
    questions = obj.get("questions", []) if isinstance(obj, dict) else []
    if not isinstance(questions, list):
        return "questions er ikke en liste."

    start = 1 if page_from is None else page_from
    end = 10**9 if page_to is None else page_to

    for q in questions:
        try:
            source_page = int(q.get("source_page", 0))
        except Exception:
            return "Et spørgsmål har ugyldig kildeside."

        if source_page and not (start <= source_page <= end):
            return "Et spørgsmål bruger en kildeside uden for det valgte sideinterval."

    return None
