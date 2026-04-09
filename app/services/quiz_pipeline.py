"""
quiz_pipeline.py

Generator til quiz-forslag fra PDF:
- læser tekst fra PDF
- bruger OCR på billeder/illustrationer
- kalder Ollama
- validerer JSON
- reparerer eller regenererer ved fejl

Kun multiple choice:
- 3 svarmuligheder
- 1 korrekt svar
- dansk sprog
- alderstilpasset formulering

Flow:
- generér flere kandidatspørgsmål end nødvendigt
- underviser vælger de bedste
- valgte spørgsmål finaliseres til en quiz
"""

import json
import random
import re
import time
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple

import pdfplumber
import requests

try:
    import pytesseract
    from pdf2image import convert_from_bytes

    OCR_AVAILABLE = True
except Exception:
    OCR_AVAILABLE = False


OLLAMA_HOST = "http://localhost:11434"
OLLAMA_CHAT_URL = f"{OLLAMA_HOST}/api/chat"
OLLAMA_TAGS_URL = f"{OLLAMA_HOST}/api/tags"


@dataclass
class OCRParams:
    use_ocr: bool = True
    max_pages: int = 12
    dpi: int = 160
    lang: str = "eng"


@dataclass
class QuizGenParams:
    model: str = "llama3.2:latest"
    num_questions: int = 10
    age_group: str = "B"
    page_from: Optional[int] = None
    page_to: Optional[int] = None

    max_pages: int = 120
    max_chars: int = 22000

    temperature: float = 0.2
    timeout: int = 420
    num_ctx: int = 4096
    num_predict: int = 2200

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


def norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


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
    dpi: int = 160,
    lang: str = "eng",
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
            parts.append(f"[OCR side {idx}]\n{txt}")

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

    s2 = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.IGNORECASE | re.MULTILINE).strip()

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


def age_group_description(age_group: str) -> str:
    mapping = {
        "A": (
            "Hold A: 6-8 år. Brug meget enkelt, konkret og børnevenligt sprog. "
            "Korte sætninger. Undgå svære fagord, medmindre de forklares helt enkelt."
        ),
        "B": (
            "Hold B: 9-12 år. Brug enkelt og tydeligt dansk. "
            "Gør svære formuleringer lettere at forstå for børn."
        ),
        "C": (
            "Hold C: 13-15 år. Brug tydeligt og alderssvarende dansk. "
            "Fagord må bruges, hvis de stadig er lette at forstå."
        ),
        "UNG": (
            "Ungdomshold: 16-18 år. Brug mere moden og direkte dansk formulering, "
            "men stadig klart og pædagogisk."
        ),
    }
    return mapping.get(age_group.upper(), "Tilpas sproget til aldersgruppen.")


def candidate_count(requested: int) -> int:
    """
    Lav flere forslag end nødvendigt, så underviseren kan vælge.
    """
    return max(requested + 4, int(round(requested * 1.8)))

def question_signature(question: Dict[str, Any]) -> str:
    q = norm_ws(str(question.get("question", ""))).lower()
    q = re.sub(r"[^a-zæøå0-9 ]", "", q)
    return q


def filter_out_similar_questions(questions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    unique = []

    for q in questions:
        sig = question_signature(q)
        if not sig:
            continue
        if sig in seen:
            continue
        seen.add(sig)
        unique.append(q)

    return unique


def build_prompt(material: str, params: QuizGenParams, n_candidates: int) -> str:
    age_desc = age_group_description(params.age_group)

    return f"""
Du er en erfaren underviser, børneformidler og quiz-designer.

Du skal skrive ALT på dansk.

Kontekst:
- Quizzen skal bruges i fælles undervisning for børn og unge.
- Det er ikke en eksamen.
- Formålet er engagement, deltagelse og repetition.
- Ét spørgsmål vises ad gangen på en skærm.
- Sproget skal tilpasses målgruppen og må gerne være lettere end teksten i PDF-materialet.
- Du skal lave FLERE forslag, så en underviser senere kan vælge de bedste spørgsmål.

Målgruppe:
{age_desc}

Opgave:
Lav præcis {n_candidates} multiple choice-spørgsmål ud fra materialet.

Meget vigtige regler:
- Brug kun viden, som kan udledes af materialet.
- Det korrekte svar skal være støttet af materialet.
- Du må gerne omskrive indholdet til lettere dansk.
- Hvert spørgsmål skal være tydeligt og let at forstå.
- Spørgsmålene må ikke være næsten identiske.
- Undgå at flere spørgsmål dækker præcis samme pointe.
- Returnér ikke labels, kategorinavne eller context-felter.

Regler for spørgsmål:
- Du må gerne lave spørgsmål ud fra ting, der står i lister.
- Men hvis det korrekte svar er et punkt fra en liste i materialet, må de forkerte svar IKKE være andre punkter fra den samme liste.
- De forkerte svar må heller ikke være andre oplysninger fra materialet, som også er korrekte.
- Undgå spørgsmål om rækkefølge, fx "hvad kommer først", medmindre materialet tydeligt angiver en rækkefølge.
- Undgå spørgsmål, hvor flere svar kan være teknisk rigtige.

Regler for svarmuligheder:
- Hvert spørgsmål skal have præcis 3 svarmuligheder.
- Der må kun være 1 korrekt svar.
- De 2 forkerte svar skal du selv konstruere.
- De 2 forkerte svar skal være plausible og ligge inden for samme emneområde som spørgsmålet.
- De 2 forkerte svar må ikke være absurde, fjollede eller helt irrelevante.
- De 2 forkerte svar må ikke være delvist korrekte.
- De 2 forkerte svar må ikke stå i PDF'en som korrekte oplysninger.
- Undgå svarmuligheder, der næsten er ens.
- Undgå "Alle ovenstående", "Ingen af ovenstående", "Ved ikke" og lignende.

Regler for forklaring:
- Hver forklaring skal være konkret.
- Forklaringen må ikke være tom.
- Forklaringen skal kort sige, hvorfor det rigtige svar er rigtigt.

Returnér KUN gyldig JSON i dette schema:
{{
  "quiz_title": "Spørgsmålsforslag",
  "questions": [
    {{
      "id": 1,
      "type": "mcq",
      "question": "spørgsmål på dansk",
      "options": ["svar 1", "svar 2", "svar 3"],
      "answer_index": 0,
      "explanation": "kort forklaring på dansk"
    }}
  ]
}}

Materiale:
\"\"\"
{material}
\"\"\"
""".strip()


def build_repair_prompt(model_output: str, n_candidates: int) -> str:
    return f"""
Du skal rette model-outputtet og returnere KUN gyldig JSON.

Skriv ALT på dansk.

Regler:
- Ingen markdown
- Ingen forklaringer uden for JSON
- Ingen tekst før eller efter JSON
- JSON skal være et objekt
- Objektet SKAL indeholde:
  - "quiz_title"
  - "questions"
- "questions" SKAL være en liste med præcis {n_candidates} spørgsmål
- Hvert spørgsmål skal være type "mcq"
- Hvert spørgsmål skal have præcis 3 svarmuligheder
- Der må kun være 1 korrekt svar
- answer_index skal være 0, 1 eller 2
- Der må ikke være et context-felt
- Forklaringen må ikke være tom
- Hvis et spørgsmål er lavet ud fra en liste, må de forkerte svar ikke være andre rigtige punkter fra samme liste
- De forkerte svar må ikke være andre korrekte oplysninger fra materialet

Schema:
{{
  "quiz_title": "Spørgsmålsforslag",
  "questions": [
    {{
      "id": 1,
      "type": "mcq",
      "question": "spørgsmål på dansk",
      "options": ["svar 1", "svar 2", "svar 3"],
      "answer_index": 0,
      "explanation": "kort forklaring på dansk"
    }}
  ]
}}

MODEL OUTPUT DER SKAL RETTES:
{model_output}
""".strip()


def build_regenerate_prompt(material: str, params: QuizGenParams, n_candidates: int) -> str:
    age_desc = age_group_description(params.age_group)

    return f"""
Du skal generere en helt ny liste af spørgmålsforslag fra bunden og returnere KUN gyldig JSON.

Du skal skrive ALT på dansk.

Målgruppe:
{age_desc}

Lav præcis {n_candidates} multiple choice-spørgsmål.

Regler:
- JSON skal være et objekt med felterne "quiz_title" og "questions"
- "questions" må IKKE være tom
- Brug let og alderspasset dansk
- Hvert spørgsmål skal have præcis 3 svarmuligheder
- Der må kun være 1 korrekt svar
- De 2 forkerte svar skal være plausible, men forkerte
- De forkerte svar må ikke være absurde
- De forkerte svar må ikke være delvist korrekte
- De forkerte svar må ikke være andre korrekte oplysninger fra materialet
- Hvis spørgsmålet bygger på en liste, må de forkerte svar ikke være andre rigtige punkter fra samme liste
- Undgå næsten identiske spørgsmål
- Undgå gentagne emner
- Ingen context-felt
- Ingen markdown
- Ingen ekstra tekst
- Hver forklaring skal være konkret og ikke tom

Returnér KUN dette schema:
{{
  "quiz_title": "Spørgsmålsforslag",
  "questions": [
    {{
      "id": 1,
      "type": "mcq",
      "question": "spørgsmål på dansk",
      "options": ["svar 1", "svar 2", "svar 3"],
      "answer_index": 0,
      "explanation": "kort forklaring på dansk"
    }}
  ]
}}

Materiale:
\"\"\"
{material}
\"\"\"
""".strip()


def build_review_prompt(quiz_obj: Dict[str, Any], material: str, params: QuizGenParams, n_candidates: int) -> str:
    quiz_json = json.dumps(quiz_obj, ensure_ascii=False, indent=2)
    age_desc = age_group_description(params.age_group)

    return f"""
Du er kvalitetskontrol for en liste af spørgmålsforslag.

Du skal skrive ALT på dansk og returnere KUN gyldig JSON.

Målgruppe:
{age_desc}

Din opgave:
Gennemgå forslagene og ret dem, hvis der er problemer.

Tjek især dette:
- Dublerer spørgsmål hinanden?
- Er flere svarmuligheder teknisk korrekte?
- Er de forkerte svar bare andre rigtige punkter fra samme liste?
- Er de forkerte svar nævnt i materialet som korrekte oplysninger?
- Er svarmulighederne for mærkelige, kunstige eller klodsede?
- Er forklaringerne tomme eller for dårlige?
- Er sproget passende til målgruppen?

Regler:
- Behold præcis {n_candidates} spørgsmål.
- Hvert spørgsmål skal være type "mcq".
- Hvert spørgsmål skal have præcis 3 svarmuligheder.
- Der må kun være 1 korrekt svar.
- Hvis et spørgsmål er dårligt, så skriv det om eller erstat det.
- Hvis to spørgsmål ligner hinanden for meget, så behold kun ét af dem og omskriv det andet til noget nyt.
- Hvis et spørgsmål bygger på en liste, må de forkerte svar ikke være andre korrekte punkter fra listen.
- De forkerte svar må gerne være opfundne, men de skal være plausible og klart forkerte.
- Der må ikke være context-felt.
- Forklaringer skal være korte, konkrete og på dansk.

Returnér KUN JSON i dette schema:
{{
  "quiz_title": "Spørgsmålsforslag",
  "questions": [
    {{
      "id": 1,
      "type": "mcq",
      "question": "spørgsmål på dansk",
      "options": ["svar 1", "svar 2", "svar 3"],
      "answer_index": 0,
      "explanation": "kort forklaring på dansk"
    }}
  ]
}}

Forslag der skal gennemgås:
{quiz_json}

Materiale:
\"\"\"
{material}
\"\"\"
""".strip()

def build_single_regenerate_prompt(
    material: str,
    params: QuizGenParams,
    old_question: Dict[str, Any],
    existing_questions: List[Dict[str, Any]],
) -> str:
    age_desc = age_group_description(params.age_group)
    old_q_json = json.dumps(old_question, ensure_ascii=False, indent=2)
    existing_json = json.dumps(existing_questions, ensure_ascii=False, indent=2)

    return f"""
Du skal skrive ALT på dansk.

Du skal lave PRÆCIS 1 nyt multiple choice-spørgsmål ud fra materialet.

Målgruppe:
{age_desc}

Vigtigt:
- Det nye spørgsmål må IKKE ligne det gamle spørgsmål for meget.
- Det nye spørgsmål må IKKE ligne de eksisterende spørgsmål for meget.
- Det nye spørgsmål skal handle om et andet aspekt eller en anden pointe i materialet.
- Brug kun viden, som kan udledes af materialet.
- Det korrekte svar skal være støttet af materialet.
- De 2 forkerte svar skal være plausible, men forkerte.
- De 2 forkerte svar må ikke være andre korrekte oplysninger fra materialet.
- Hvis spørgsmålet bygger på en liste, må de forkerte svar ikke være andre rigtige punkter fra samme liste.
- Hvert spørgsmål skal have præcis 3 svarmuligheder.
- Der må kun være 1 korrekt svar.
- Forklaringen skal være konkret og på dansk.
- Ingen context-felt.
- Ingen markdown.
- Ingen ekstra tekst.

Returnér KUN gyldig JSON i dette schema:
{{
  "quiz_title": "Spørgsmålsforslag",
  "questions": [
    {{
      "id": 1,
      "type": "mcq",
      "question": "spørgsmål på dansk",
      "options": ["svar 1", "svar 2", "svar 3"],
      "answer_index": 0,
      "explanation": "kort forklaring på dansk"
    }}
  ]
}}

Gammelt spørgsmål der skal erstattes:
{old_q_json}

Eksisterende spørgsmål som det nye ikke må ligne for meget:
{existing_json}

Materiale:
\"\"\"
{material}
\"\"\"
""".strip()


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
        }

        if not item["explanation"]:
            item["explanation"] = "Forklaring mangler."

        opts = q.get("options", [])
        if not isinstance(opts, list):
            opts = []

        opts = [str(x).strip() for x in opts if str(x).strip()]

        unique_opts: List[str] = []
        seen = set()
        for o in opts:
            key = norm_ws(o).lower()
            if key not in seen:
                unique_opts.append(o)
                seen.add(key)

        fallback_options = [
            "Det passer ikke med det, materialet forklarer",
            "Det beskriver noget andet inden for emnet",
            "Det er ikke den rigtige forklaring i denne sammenhæng",
        ]

        j = 0
        while len(unique_opts) < 3:
            candidate = fallback_options[j % len(fallback_options)]
            key = norm_ws(candidate).lower()
            if key not in seen:
                unique_opts.append(candidate)
                seen.add(key)
            j += 1

        unique_opts = unique_opts[:3]

        try:
            ai = int(q.get("answer_index", 0))
        except Exception:
            ai = 0

        if ai not in [0, 1, 2]:
            ai = 0

        item["options"] = unique_opts
        item["answer_index"] = ai

        normalized_questions.append(item)

    normalized_questions = filter_out_similar_questions(normalized_questions)
    obj["questions"] = normalized_questions
    return obj


def validate_quiz_schema(obj: Dict[str, Any], expected_count: Optional[int] = None) -> Optional[str]:
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

        if q.get("type") != "mcq":
            return "Kun 'mcq' er tilladt."

        for key in ["id", "question", "options", "answer_index", "explanation"]:
            if key not in q:
                return f"Spørgsmål mangler feltet '{key}'."

        if len(norm_ws(str(q["question"]))) < 8:
            return "Et spørgsmål er for kort eller uklart."

        if not isinstance(q["options"], list) or len(q["options"]) != 3:
            return "MCQ skal have præcis 3 svarmuligheder."

        if q["answer_index"] not in [0, 1, 2]:
            return "answer_index skal være 0-2."

        option_norms = [norm_ws(str(o)).lower() for o in q["options"]]
        if len(set(option_norms)) < 3:
            return "Svarmuligheder er for ens eller duplikerede."

        explanation = norm_ws(str(q["explanation"]))
        if len(explanation) < 10:
            return "Forklaringen er for kort eller mangler."

    return None


def validate_mcq_quality(obj: Dict[str, Any]) -> Optional[str]:
    questions = obj.get("questions", [])
    if not isinstance(questions, list):
        return "questions er ikke en liste."

    seen_question_keys = set()

    for q in questions:
        if q.get("type") != "mcq":
            continue

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
            return "Svarmuligheder er for ens."

        correct_idx = q.get("answer_index")
        if correct_idx not in [0, 1, 2]:
            return "answer_index skal være 0-2."

        correct = norm_options[correct_idx]
        wrongs = [opt for i, opt in enumerate(norm_options) if i != correct_idx]

        if correct in wrongs:
            return "Korrekt svar optræder også som forkert svar."

        banned_fragments = [
            "ingen af ovenstående",
            "alle ovenstående",
            "ved ikke",
            "ukendt",
        ]
        for opt in norm_options:
            for fragment in banned_fragments:
                if fragment in opt:
                    return "For generiske eller uegnede svarmuligheder fundet."

    return None


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
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_ctx": num_ctx,
            "num_predict": num_predict,
        },
    }

    response = requests.post(OLLAMA_CHAT_URL, json=payload, timeout=timeout)
    response.raise_for_status()

    data = response.json()
    return data["message"]["content"]


def run_review_step(
    quiz_obj: Dict[str, Any],
    material: str,
    params: QuizGenParams,
    expected_count: int,
) -> Optional[Dict[str, Any]]:
    reviewed_output = ollama_chat(
        model=params.model,
        messages=[
            {
                "role": "system",
                "content": "Returnér kun gyldig JSON. Skriv alt på dansk.",
            },
            {
                "role": "user",
                "content": build_review_prompt(quiz_obj, material, params, expected_count),
            },
        ],
        temperature=0.0,
        timeout=min(240, params.timeout),
        num_ctx=params.num_ctx,
        num_predict=1600,
    )

    obj = safe_json_loads(reviewed_output)
    if not obj:
        return None

    obj = normalize_quiz_obj(obj)
    obj = shuffle_questions(obj)
    return obj


def generate_quiz_candidates_from_pdf(
    pdf_bytes: bytes,
    params: QuizGenParams,
) -> Tuple[Dict[str, Any], QuizGenDebug]:
    try:
        requests.get(OLLAMA_TAGS_URL, timeout=3).raise_for_status()
    except Exception as e:
        raise RuntimeError(f"Kan ikke nå Ollama på {OLLAMA_HOST}. Fejl: {e}")

    base_text = extract_text_pdfplumber(
        pdf_bytes=pdf_bytes,
        page_from=params.page_from,
        page_to=params.page_to,
        max_pages=params.max_pages,
    )

    material_parts: List[str] = []
    if base_text.strip():
        material_parts.append(base_text)

    used_ocr = False
    if params.ocr.use_ocr and OCR_AVAILABLE:
        try:
            ocr_text = extract_text_ocr(
                pdf_bytes=pdf_bytes,
                page_from=params.page_from,
                page_to=params.page_to,
                max_pages=params.ocr.max_pages,
                dpi=params.ocr.dpi,
                lang=params.ocr.lang,
            )
            if ocr_text.strip():
                used_ocr = True
                material_parts.append(ocr_text)
        except Exception:
            pass

    material = "\n\n".join(material_parts).strip()
    if not material:
        raise RuntimeError("Kunne ikke udtrække tekst fra PDF.")

    material = material[: params.max_chars]
    n_candidates = candidate_count(params.num_questions)
    prompt = build_prompt(material, params, n_candidates)

    start_time = time.time()
    model_output = ollama_chat(
        model=params.model,
        messages=[
            {
                "role": "system",
                "content": "Svar kun med gyldig JSON. Skriv alt på dansk.",
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        temperature=params.temperature,
        timeout=params.timeout,
        num_ctx=params.num_ctx,
        num_predict=params.num_predict,
    )
    latency = time.time() - start_time

    did_repair = False
    raw_output_final = model_output

    obj = safe_json_loads(model_output)
    if obj:
        obj = normalize_quiz_obj(obj)
        obj = shuffle_questions(obj)

    err = validate_quiz_schema(obj, expected_count=n_candidates) if obj else "Kunne ikke parse JSON."
    if not err and obj:
        err = validate_mcq_quality(obj)

    if err:
        did_repair = True

        repaired_output = ollama_chat(
            model=params.model,
            messages=[
                {
                    "role": "system",
                    "content": "Returnér kun gyldig JSON. Skriv alt på dansk.",
                },
                {
                    "role": "user",
                    "content": build_repair_prompt(model_output, n_candidates),
                },
            ],
            temperature=0.0,
            timeout=min(240, params.timeout),
            num_ctx=params.num_ctx,
            num_predict=1400,
        )

        raw_output_final = repaired_output

        obj2 = safe_json_loads(repaired_output)
        if obj2:
            obj2 = normalize_quiz_obj(obj2)
            obj2 = shuffle_questions(obj2)

        err2 = validate_quiz_schema(obj2, expected_count=n_candidates) if obj2 else "Kunne ikke parse repareret JSON."
        if not err2 and obj2:
            err2 = validate_mcq_quality(obj2)

        if err2:
            regenerated_output = ollama_chat(
                model=params.model,
                messages=[
                    {
                        "role": "system",
                        "content": "Returnér kun gyldig JSON. Skriv alt på dansk.",
                    },
                    {
                        "role": "user",
                        "content": build_regenerate_prompt(material, params, n_candidates),
                    },
                ],
                temperature=0.1,
                timeout=min(300, params.timeout),
                num_ctx=params.num_ctx,
                num_predict=2200,
            )

            raw_output_final = regenerated_output

            obj3 = safe_json_loads(regenerated_output)
            if obj3:
                obj3 = normalize_quiz_obj(obj3)
                obj3 = shuffle_questions(obj3)

            err3 = validate_quiz_schema(obj3, expected_count=n_candidates) if obj3 else "Kunne ikke parse regenereret JSON."
            if not err3 and obj3:
                err3 = validate_mcq_quality(obj3)

            if err3:
                raise RuntimeError(f"Spørgsmålsforslag er stadig ugyldige efter repair: {err3}")

            obj = obj3
        else:
            obj = obj2

    reviewed_obj = run_review_step(obj, material, params, n_candidates)
    if reviewed_obj:
        review_err = validate_quiz_schema(reviewed_obj, expected_count=n_candidates)
        if not review_err:
            review_err = validate_mcq_quality(reviewed_obj)
        if not review_err:
            obj = reviewed_obj

    page_range = f"{params.page_from or 1}-{params.page_to or 'sidste'}"

    debug = QuizGenDebug(
        extracted_chars=len(material),
        used_ocr=used_ocr,
        material_preview=material[:4000],
        model_latency_s=latency,
        did_repair=did_repair,
        raw_model_output=raw_output_final,
        page_range=page_range,
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

    base_text = extract_text_pdfplumber(
        pdf_bytes=pdf_bytes,
        page_from=params.page_from,
        page_to=params.page_to,
        max_pages=params.max_pages,
    )

    material_parts: List[str] = []
    if base_text.strip():
        material_parts.append(base_text)

    if params.ocr.use_ocr and OCR_AVAILABLE:
        try:
            ocr_text = extract_text_ocr(
                pdf_bytes=pdf_bytes,
                page_from=params.page_from,
                page_to=params.page_to,
                max_pages=params.ocr.max_pages,
                dpi=params.ocr.dpi,
                lang=params.ocr.lang,
            )
            if ocr_text.strip():
                material_parts.append(ocr_text)
        except Exception:
            pass

    material = "\n\n".join(material_parts).strip()
    if not material:
        raise RuntimeError("Kunne ikke udtrække tekst fra PDF.")

    material = material[: params.max_chars]

    prompt = build_single_regenerate_prompt(
        material=material,
        params=params,
        old_question=old_question,
        existing_questions=existing_questions,
    )

    output = ollama_chat(
        model=params.model,
        messages=[
            {
                "role": "system",
                "content": "Returnér kun gyldig JSON. Skriv alt på dansk.",
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        temperature=0.2,
        timeout=params.timeout,
        num_ctx=params.num_ctx,
        num_predict=1200,
    )

    obj = safe_json_loads(output)
    if not obj:
        raise RuntimeError("Kunne ikke parse JSON for nyt spørgsmål.")

    obj = normalize_quiz_obj(obj)

    err = validate_quiz_schema(obj, expected_count=1)
    if err:
        raise RuntimeError(f"Nyt spørgsmål er ugyldigt: {err}")

    err2 = validate_mcq_quality(obj)
    if err2:
        raise RuntimeError(f"Nyt spørgsmål har kvalitetsfejl: {err2}")

    q = obj["questions"][0]

    old_sig = question_signature(old_question)
    new_sig = question_signature(q)
    existing_sigs = {question_signature(item) for item in existing_questions}

    if new_sig == old_sig:
        raise RuntimeError("Det nye spørgsmål ligner for meget det gamle spørgsmål.")

    if new_sig in existing_sigs:
        raise RuntimeError("Det nye spørgsmål ligner for meget et eksisterende spørgsmål.")

    return q

def finalize_selected_questions(selected_questions: List[Dict[str, Any]], quiz_title: str = "Valgt quiz") -> Dict[str, Any]:
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

    return quiz_obj