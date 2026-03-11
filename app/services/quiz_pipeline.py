"""
quiz_pipeline.py
Core quiz-generator (UI-uafhængig) til at:
- Udtrække tekst fra PDF (pdfplumber)
- OCR fallback (valgfrit)
- Kalde Ollama /api/chat
- Parse + validere JSON schema
- Repair hvis modellen returnerer invalid JSON

Brug:
from app.services.quiz_pipeline import generate_quiz_from_pdf, QuizGenParams, OCRParams
"""

import json
import re
import time
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple

import pdfplumber
import requests


# ----------------------------
# Optional OCR deps
# ----------------------------
try:
    import pytesseract
    from pdf2image import convert_from_bytes

    OCR_AVAILABLE = True
except Exception:
    OCR_AVAILABLE = False


# ----------------------------
# Ollama config
# ----------------------------
OLLAMA_HOST = "http://localhost:11434"
OLLAMA_CHAT_URL = f"{OLLAMA_HOST}/api/chat"
OLLAMA_TAGS_URL = f"{OLLAMA_HOST}/api/tags"


# ----------------------------
# Params + result types
# ----------------------------
@dataclass
class OCRParams:
    use_ocr: bool = False
    max_pages: int = 6
    dpi: int = 160
    lang: str = "eng"


@dataclass
class QuizGenParams:
    model: str = "llama3.2:latest"
    num_questions: int = 10
    focus: str = "Blandet"
    bloom: str = "Understand"

    max_pages: int = 120          # pdfplumber pages
    max_chars: int = 18000        # truncate text sent to model

    temperature: float = 0.2
    timeout: int = 420            # seconds for ollama request
    num_ctx: int = 2048
    num_predict: int = 700

    ocr: OCRParams = field(default_factory=OCRParams)


@dataclass
class QuizGenDebug:
    extracted_chars: int
    used_ocr: bool
    material_preview: str
    model_latency_s: float
    did_repair: bool
    raw_model_output: str


# ----------------------------
# Helpers
# ----------------------------
def norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def extract_text_pdfplumber(pdf_bytes: bytes, max_pages: int = 50) -> str:
    out: List[str] = []
    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        for i, page in enumerate(pdf.pages[:max_pages], start=1):
            t = (page.extract_text() or "").strip()
            if t:
                out.append(f"[Page {i}]\n{t}")
    return "\n\n".join(out)


def extract_text_ocr(pdf_bytes: bytes, max_pages: int = 6, dpi: int = 160, lang: str = "eng") -> str:
    if not OCR_AVAILABLE:
        return ""
    images = convert_from_bytes(pdf_bytes, dpi=dpi, first_page=1, last_page=max_pages)
    parts: List[str] = []
    for i, img in enumerate(images, start=1):
        txt = (pytesseract.image_to_string(img, lang=lang) or "").strip()
        if txt:
            parts.append(f"[OCR Page {i}]\n{txt}")
    return "\n\n".join(parts)


def safe_json_loads(s: str) -> Optional[Dict[str, Any]]:
    s = (s or "").strip()
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        pass

    # salvage JSON object from surrounding text
    m = re.search(r"\{.*\}", s, flags=re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None


def validate_quiz_schema(obj: Dict[str, Any]) -> Optional[str]:
    if not isinstance(obj, dict):
        return "JSON er ikke et objekt."

    if "questions" not in obj or not isinstance(obj["questions"], list) or len(obj["questions"]) == 0:
        return "JSON mangler 'questions' eller listen er tom."

    for q in obj["questions"]:
        if not isinstance(q, dict):
            return "Et spørgsmål er ikke et objekt."

        required = ["id", "type", "context", "question", "options", "answer_index", "explanation"]
        for key in required:
            if key not in q:
                return f"Spørgsmål mangler feltet '{key}'."

        if q["type"] != "mcq":
            return "Kun 'mcq' understøttes."

        if not isinstance(q["options"], list) or len(q["options"]) != 4:
            return "options skal være en liste med præcis 4 svar."

        if q["answer_index"] not in [0, 1, 2, 3]:
            return "answer_index skal være 0-3."

        if len(norm_ws(str(q["question"]))) < 12:
            return "Et spørgsmål er for kort/uklart."

        opt_norm = [norm_ws(str(o)).lower() for o in q["options"]]
        if len(set(opt_norm)) < 4:
            return "Svarmuligheder er for ens/duplikerede."

    return None


def normalize_quiz_obj(obj: Dict[str, Any]) -> Dict[str, Any]:
    """
    Gør output robust ved at udfylde manglende felter og tvinge schema-krav:
    - explanation findes
    - context findes
    - options er præcis 4
    - answer_index er 0-3
    """
    if not isinstance(obj, dict):
        return obj

    questions = obj.get("questions")
    if not isinstance(questions, list):
        return obj

    def make_fallback_option(existing: List[str]) -> str:
        candidates = [
            "Det fremgår ikke af materialet",
            "Kan ikke afgøres ud fra materialet",
            "En anden mulighed",
            "Ingen af ovenstående",
        ]
        existing_norm = {norm_ws(x).lower() for x in existing if isinstance(x, str)}
        for c in candidates:
            if norm_ws(c).lower() not in existing_norm:
                return c
        return "Ingen af ovenstående"

    for q in questions:
        if not isinstance(q, dict):
            continue

        if "explanation" not in q or not str(q.get("explanation", "")).strip():
            q["explanation"] = "Forklaring mangler i model-output. (Auto-udfyldt af systemet)"

        if "context" not in q or not str(q.get("context", "")).strip():
            q["context"] = "Generelt"

        opts = q.get("options")
        if not isinstance(opts, list):
            opts = []
        opts = [str(x) for x in opts]
        opts = [o for o in opts if norm_ws(o)]

        # Make options unique (keep order)
        seen = set()
        uniq_opts = []
        for o in opts:
            key = norm_ws(o).lower()
            if key not in seen:
                uniq_opts.append(o)
                seen.add(key)
        opts = uniq_opts

        # Force exactly 4 options
        if len(opts) < 4:
            while len(opts) < 4:
                opts.append(make_fallback_option(opts))
        elif len(opts) > 4:
            # try keep correct answer
            ai = q.get("answer_index", 0)
            try:
                ai = int(ai)
            except Exception:
                ai = 0

            if 0 <= ai < len(opts):
                correct = opts[ai]
                first4 = opts[:4]
                if correct not in first4:
                    first4[-1] = correct
                opts = first4
                q["answer_index"] = opts.index(correct)
            else:
                opts = opts[:4]
                q["answer_index"] = 0

        q["options"] = opts

        try:
            ai = int(q.get("answer_index", 0))
        except Exception:
            ai = 0
        if ai not in [0, 1, 2, 3]:
            ai = 0
        q["answer_index"] = ai

        if "question" in q and isinstance(q["question"], str):
            q["question"] = q["question"].strip()

    return obj


# ----------------------------
# Ollama calls
# ----------------------------
def ollama_list_models(timeout: int = 5) -> List[str]:
    r = requests.get(OLLAMA_TAGS_URL, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    return [m["name"] for m in data.get("models", [])]


def ollama_chat(
    model: str,
    messages: List[Dict[str, str]],
    temperature: float = 0.2,
    timeout: int = 180,
    num_ctx: int = 4096,
    num_predict: int = 900,
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
    r = requests.post(OLLAMA_CHAT_URL, json=payload, timeout=timeout)
    r.raise_for_status()
    return r.json()["message"]["content"]


# ----------------------------
# Prompt builders
# ----------------------------
def build_prompt(material: str, n: int, focus: str, bloom: str) -> str:
    return f"""
Du er en erfaren underviser og quiz-designer.

Opgave:
Lav PRÆCIS {n} multiple-choice spørgsmål (MCQ) ud fra materialet.

Kvalitetskrav (vigtigt):
- Spørgsmålene skal teste forståelse af indhold (ikke tilfældige “fill in the blank”-ord).
- Omskriv slide-fragmenter til hele, naturlige sætninger.
- Hvert spørgsmål skal have tydelig kontekst (fx "Scrum roller", "Definition", "Proces", "Princip").
- PRÆCIS 4 svarmuligheder.
- PRÆCIS 1 korrekt.

MEGET VIGTIGT:
- Spørgsmålet skal være formuleret så der KUN kan være ét korrekt svar.
- Undgå "Hvem er en af..." / "Hvilke af følgende..." (giver flere korrekte).
- Brug mere specifikke formuleringer: "Hvilken rolle har ansvaret for X?" / "Hvad beskriver bedst Y?"

Svarmuligheder:
- Distraktorer skal være plausible men tydeligt forkerte ift. materialet.
- Undgå "Alle ovenstående" / "Ingen af ovenstående" medmindre absolut nødvendigt.

Fokus: {focus}
Bloom: {bloom}

Returnér KUN gyldig JSON i dette format:
{{
  "quiz_title": "string",
  "questions": [
    {{
      "id": 1,
      "type": "mcq",
      "context": "kort label",
      "question": "spørgsmål",
      "options": ["A ...", "B ...", "C ...", "D ..."],
      "answer_index": 0,
      "explanation": "kort forklaring"
    }}
  ]
}}

Materiale:
\"\"\"
{material}
\"\"\"
""".strip()


def build_repair_prompt(model_output: str) -> str:
    return f"""
Du skal returnere KUN gyldig JSON der matcher schemaet præcist.
Ingen kommentarer. Ingen ekstra tekst.

KRAV:
- PRÆCIS 4 options pr spørgsmål
- PRÆCIS 1 korrekt
- INGEN spørgsmål der implicit har flere korrekte svar (undgå "en af", "hvilke af følgende")

Schema:
{{
  "quiz_title": "string",
  "questions": [
    {{
      "id": 1,
      "type": "mcq",
      "context": "kort label",
      "question": "spørgsmål",
      "options": ["A ...", "B ...", "C ...", "D ..."],
      "answer_index": 0,
      "explanation": "kort forklaring"
    }}
  ]
}}

MODEL-OUTPUT:
{model_output}
""".strip()


# ----------------------------
# Public API: generate quiz
# ----------------------------
def generate_quiz_from_pdf(
    pdf_bytes: bytes,
    params: QuizGenParams,
) -> Tuple[Dict[str, Any], QuizGenDebug]:
    """
    Returns: (quiz_obj, debug)
    Raises: RuntimeError on failures
    """

    # 1) quick check ollama reachable
    try:
        _ = requests.get(OLLAMA_TAGS_URL, timeout=3)
    except Exception as e:
        raise RuntimeError(f"Kan ikke nå Ollama på {OLLAMA_HOST}. Kører den? Fejl: {e}")

    # 2) extract text
    base_text = extract_text_pdfplumber(pdf_bytes, max_pages=params.max_pages)

    used_ocr = False
    material_parts: List[str] = []
    if base_text.strip():
        material_parts.append(base_text)

    # OCR fallback if enabled and base_text is weak
    if params.ocr.use_ocr and len(base_text) < 1200:
        if OCR_AVAILABLE:
            used_ocr = True
            ocr_text = extract_text_ocr(
                pdf_bytes,
                max_pages=params.ocr.max_pages,
                dpi=params.ocr.dpi,
                lang=params.ocr.lang,
            )
            if ocr_text.strip():
                material_parts.append(ocr_text)

    material = "\n\n".join(material_parts).strip()
    if not material:
        raise RuntimeError("Kunne ikke udtrække tekst fra PDF (hverken tekst eller OCR).")

    # 3) truncate
    material = material[: params.max_chars]

    # 4) build prompt + call model
    prompt = build_prompt(material, params.num_questions, params.focus, params.bloom)

    t0 = time.time()
    resp = ollama_chat(
        model=params.model,
        messages=[
            {"role": "system", "content": "Svar kun med JSON i det beskrevne format. Ingen ekstra tekst."},
            {"role": "user", "content": prompt},
        ],
        temperature=params.temperature,
        timeout=params.timeout,
        num_ctx=params.num_ctx,
        num_predict=params.num_predict,
    )
    latency = time.time() - t0

    # 5) parse + normalize + validate
    did_repair = False
    obj = safe_json_loads(resp)
    if obj:
        obj = normalize_quiz_obj(obj)
    err = validate_quiz_schema(obj) if obj else "Kunne ikke parse JSON."

    raw_output_final = resp

    if err:
        # 6) repair pass
        did_repair = True
        repair_prompt = build_repair_prompt(resp)
        resp2 = ollama_chat(
            model=params.model,
            messages=[
                {"role": "system", "content": "Returnér KUN JSON, ellers tomt svar."},
                {"role": "user", "content": repair_prompt},
            ],
            temperature=0.0,
            timeout=min(240, params.timeout),
            num_ctx=params.num_ctx,
            num_predict=600,
        )
        raw_output_final = resp2

        obj2 = safe_json_loads(resp2)
        if obj2:
            obj2 = normalize_quiz_obj(obj2)
        err2 = validate_quiz_schema(obj2) if obj2 else "Kunne ikke parse repareret JSON."

        if err2:
            raise RuntimeError(f"Stadig ikke gyldig quiz-JSON efter repair: {err2}\n\nOUTPUT:\n{resp2[:3000]}")
        obj = obj2

    debug = QuizGenDebug(
        extracted_chars=len(material),
        used_ocr=used_ocr,
        material_preview=material,
        model_latency_s=latency,
        did_repair=did_repair,
        raw_model_output=raw_output_final,
    )

    return obj, debug
