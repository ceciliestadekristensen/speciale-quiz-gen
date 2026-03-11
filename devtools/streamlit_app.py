# Run: streamlit run devtools/streamlit_app.py
# Quiz prototype – clean product version (full PDF + always OCR first N pages)
# Robust JSON: salvage + repair + "continue if truncated"

import json
import re
import time
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple

import streamlit as st
import pdfplumber
import requests

# Optional OCR deps
try:
    import pytesseract
    from pdf2image import convert_from_bytes

    OCR_AVAILABLE = True
except Exception:
    OCR_AVAILABLE = False

# Local model service (hidden from UI)
OLLAMA_HOST = "http://localhost:11434"
OLLAMA_CHAT_URL = f"{OLLAMA_HOST}/api/chat"
OLLAMA_TAGS_URL = f"{OLLAMA_HOST}/api/tags"
MODEL_NAME = "llama3.2:latest"

# OCR settings (automatic, no UI)
OCR_LANG = "eng"
OCR_DPI = 160
OCR_MAX_PAGES = 12  # OCR first N pages (keeps it fast)

st.set_page_config(page_title="Quiz Builder", layout="centered")
st.title("Quiz Builder")
st.caption("Upload PDF → få en færdig quiz")

# ----------------------------
# Helpers
# ----------------------------
def norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def extract_text_pdf(pdf_bytes: bytes) -> str:
    """Læs ALLE sider fra PDF (embedded text)."""
    out = []
    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            t = (page.extract_text() or "").strip()
            if t:
                out.append(f"[Side {i}]\n{t}")
    return "\n\n".join(out)


def extract_text_ocr_first_pages(pdf_bytes: bytes, max_pages: int = OCR_MAX_PAGES) -> str:
    """OCR på de første N sider (for at fange tekst i billeder/figurer)."""
    if not OCR_AVAILABLE:
        return ""
    images = convert_from_bytes(pdf_bytes, dpi=OCR_DPI, first_page=1, last_page=max_pages)
    parts: List[str] = []
    for i, img in enumerate(images, start=1):
        txt = (pytesseract.image_to_string(img, lang=OCR_LANG) or "").strip()
        if txt:
            parts.append(f"[OCR side {i}]\n{txt}")
    return "\n\n".join(parts)


def extract_json_object(text: str) -> Optional[str]:
    """
    Trækker JSON-objekt ud af tekst, selv hvis der står andet før/efter.
    Finder første '{' og matcher til sidste '}' (greedy).
    """
    if not text:
        return None
    text = text.strip()

    # Quick path: full JSON
    if text.startswith("{") and text.endswith("}"):
        return text

    # Salvage object within text
    m = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if m:
        return m.group(0).strip()
    return None


def safe_json_loads(text: str) -> Optional[Dict[str, Any]]:
    raw = extract_json_object(text)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def looks_truncated_json(text: str) -> bool:
    """
    Heuristik: Hvis JSON starter med '{' men ikke har balancerede klammer.
    """
    raw = extract_json_object(text) or (text or "")
    if "{" not in raw:
        return False
    # Count braces in whole response (not perfect, but good enough)
    opens = raw.count("{")
    closes = raw.count("}")
    return opens > closes


def validate_quiz_schema(obj: Dict[str, Any]) -> Optional[str]:
    if not isinstance(obj, dict):
        return "JSON er ikke et objekt."

    if "questions" not in obj or not isinstance(obj["questions"], list) or len(obj["questions"]) == 0:
        return "JSON mangler 'questions' eller listen er tom."

    for q in obj["questions"]:
        required = ["id", "type", "context", "question", "options", "answer_index", "explanation"]
        for k in required:
            if k not in q:
                return f"Mangler feltet '{k}'."

        if q["type"] != "mcq":
            return "Kun MCQ understøttes."

        if not isinstance(q["options"], list) or len(q["options"]) != 4:
            return "Der skal være præcis 4 svarmuligheder."

        try:
            ai = int(q.get("answer_index", 0))
        except Exception:
            ai = 0

        if ai not in [0, 1, 2, 3]:
            ai = 0  # fallback

        q["answer_index"] = ai


        opts = [norm_ws(str(o)).lower() for o in q["options"]]
        if len(set(opts)) < 4:
            return "Svarmuligheder er for ens."

    return None


def ollama_chat(messages: List[Dict[str, str]], timeout: int = 300, num_predict: int = 1400) -> str:
    """
    num_predict hævet for at mindske risiko for "klippet JSON".
    """
    payload = {
        "model": MODEL_NAME,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": 0.2,
            "num_ctx": 4096,
            "num_predict": num_predict,
        },
    }
    r = requests.post(OLLAMA_CHAT_URL, json=payload, timeout=timeout)
    r.raise_for_status()
    return r.json()["message"]["content"]


def build_prompt(material: str, n: int) -> str:
    return f"""
Du er en erfaren underviser, eksaminator og didaktisk ekspert.

Opgave:
Lav PRÆCIS {n} multiple-choice spørgsmål ud fra materialet.

MEGET VIGTIGT – variation:
- Spørgsmålene MÅ IKKE være variationer af det samme spørgsmål.
- Brug MAKS ét spørgsmål pr. centralt begreb (fx Scrum pillars, roller, events).
- Hvert spørgsmål skal teste en FORSKELLIG type forståelse.

Fordel spørgsmålene over disse typer (så vidt muligt):
1) Definition (hvad er X?)
2) Forståelse (hvorfor findes X?)
3) Anvendelse (hvad gør man i denne situation?)
4) Konsekvens (hvad sker der hvis X mangler?)
5) Sammenligning (hvad adskiller X fra Y?)
6) Fejlforståelse (hvilket udsagn er forkert?)

Krav:
- Præcis 4 svarmuligheder
- Én korrekt
- Distraktorer skal være realistiske, ikke åbenlyst forkerte
- Undgå ja/nej, “Hvad er en af…”, eller gentagne svarmuligheder
- Brug hele sætninger, ikke enkeltord, i svarmuligheder

RETURNÉR KUN GYLDIG JSON I DETTE FORMAT:
{{
  "quiz_title": "string",
  "questions": [
    {{
      "id": 1,
      "type": "mcq",
      "context": "kort label",
      "question": "spørgsmål",
      "options": [
        "Svar A (fuld sætning)",
        "Svar B (fuld sætning)",
        "Svar C (fuld sætning)",
        "Svar D (fuld sætning)"
      ],
      "answer_index": 0,
      "explanation": "Kort forklaring på hvorfor dette svar er korrekt"
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
Returnér KUN gyldig JSON i schemaet.
Ingen ekstra tekst.

KRAV:
- PRÆCIS 4 options pr spørgsmål
- PRÆCIS 1 korrekt svar
- Alle felter: id,type,context,question,options,answer_index,explanation

MODEL-OUTPUT:
{model_output}
""".strip()


def build_continue_prompt(partial_output: str) -> str:
    return f"""
Du får et JSON-output der er blevet afbrudt midt i svaret.

Opgave:
- Fortsæt PRÆCIS hvor output stoppede
- Returnér KUN den manglende del, så det samlet bliver gyldig JSON
- Ingen forklaringer, ingen ekstra tekst

AFBRUDT OUTPUT:
{partial_output}
""".strip()


def reset_state():
    for k in list(st.session_state.keys()):
        if k.startswith("ans_"):
            del st.session_state[k]
    st.session_state.pop("quiz", None)
    st.session_state.pop("used_ocr", None)
    st.session_state.pop("debug_info", None)
    st.session_state.pop("material_preview", None)


def try_generate_quiz(material: str, num_q: int) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Robust pipeline:
    1) model -> parse
    2) if truncated -> continue -> parse
    3) if invalid -> repair -> parse
    """
    prompt = build_prompt(material, num_q)

    t0 = time.time()
    resp = ollama_chat(
        messages=[
            {"role": "system", "content": "Svar KUN med gyldig JSON."},
            {"role": "user", "content": prompt},
        ],
        timeout=360,
        num_predict=1400,
    )
    latency = time.time() - t0

    # Attempt 1: salvage + parse
    obj = safe_json_loads(resp)
    if obj:
        err = validate_quiz_schema(obj)
        if not err:
            return obj, {"latency_s": latency, "path": "direct"}

    # Attempt 1b: if looks truncated, ask to continue and append
    combined = resp
    if looks_truncated_json(resp):
        resp_cont = ollama_chat(
            messages=[
                {"role": "system", "content": "Svar KUN med tekst der fortsætter JSON."},
                {"role": "user", "content": build_continue_prompt(resp)},
            ],
            timeout=240,
            num_predict=900,
        )
        combined = resp + "\n" + resp_cont

        obj2 = safe_json_loads(combined)
        if obj2:
            err2 = validate_quiz_schema(obj2)
            if not err2:
                return obj2, {"latency_s": latency, "path": "continued"}

    # Attempt 2: repair pass (using combined if we tried continue)
    resp2 = ollama_chat(
        messages=[
            {"role": "system", "content": "Returnér KUN JSON."},
            {"role": "user", "content": build_repair_prompt(combined)},
        ],
        timeout=300,
        num_predict=1200,
    )

    # Attempt: salvage JSON from repair output too
    obj3 = safe_json_loads(resp2)

    # If still not parseable, try to salvage a JSON object from text
    if not obj3:
        raw = extract_json_object(resp2)  # returns "{...}" if it exists inside
        if raw:
            try:
                obj3 = json.loads(raw)
            except Exception:
                obj3 = None

    if not obj3:
        snippet = (resp2 or "")[:800]
        raise RuntimeError("Kunne ikke parse repareret JSON. Model-output start:\n\n" + snippet)

    err3 = validate_quiz_schema(obj3)
    if err3:
        raise RuntimeError(f"Repareret JSON er stadig ikke gyldigt: {err3}")

    return obj3, {"latency_s": latency, "path": "repaired"}


# ----------------------------
# UI
# ----------------------------
uploaded = st.file_uploader("Upload PDF", type=["pdf"])
num_q = st.slider("Antal spørgsmål", 3, 20, 10)
show_preview = st.checkbox("Vis material preview (debug)", value=False)

st.divider()

if st.button("Generér quiz", type="primary", disabled=not uploaded):
    reset_state()

    # check model service reachable
    try:
        _ = requests.get(OLLAMA_TAGS_URL, timeout=3)
    except Exception:
        st.error("Kan ikke starte quiz-motoren. Sørg for at den kører.")
        st.stop()

    pdf_bytes = uploaded.getvalue()
    st.caption(f"Filstørrelse: {len(pdf_bytes)/1024/1024:.1f} MB")

    with st.spinner("Læser PDF (tekst)..."):
        base_text = extract_text_pdf(pdf_bytes)

    ocr_text = ""
    used_ocr = False
    if OCR_AVAILABLE:
        used_ocr = True
        with st.spinner(f"OCR (billeder) – første {OCR_MAX_PAGES} sider..."):
            try:
                ocr_text = extract_text_ocr_first_pages(pdf_bytes, max_pages=OCR_MAX_PAGES)
            except Exception as e:
                used_ocr = False
                st.warning(f"OCR fejlede: {e}")
    else:
        st.info("OCR er ikke installeret (pytesseract/pdf2image mangler). Fortsætter med PDF-tekst.")

    material = "\n\n".join([t for t in [base_text, ocr_text] if t and t.strip()]).strip()
    if not material:
        st.error("Kunne ikke læse indhold fra PDF.")
        st.stop()

    if show_preview:
        st.session_state["material_preview"] = material

    # Generate
    try:
        with st.spinner("Genererer quiz..."):
            quiz, dbg = try_generate_quiz(material, num_q)
    except Exception as e:
        st.error(f"Fejl: {e}")
        st.stop()

    st.session_state["quiz"] = quiz
    st.session_state["used_ocr"] = used_ocr
    st.session_state["debug_info"] = {
        **dbg,
        "ocr_used": used_ocr,
        "ocr_pages": OCR_MAX_PAGES if used_ocr else 0,
        "pdf_text_chars": len(base_text or ""),
        "ocr_text_chars": len(ocr_text or ""),
        "total_chars": len(material),
    }


# ----------------------------
# Render
# ----------------------------
if show_preview and st.session_state.get("material_preview"):
    with st.expander("Material preview (det modellen så)"):
        st.text_area("Materiale", st.session_state["material_preview"], height=220)

quiz = st.session_state.get("quiz")
if quiz:
    st.divider()
    st.header(quiz.get("quiz_title", "Quiz"))

    dbg = st.session_state.get("debug_info") or {}
    st.caption(
        f"Debug: {dbg.get('path','?')} | latency {dbg.get('latency_s', 0):.1f}s | "
        f"PDF-tekst {dbg.get('pdf_text_chars', 0)} tegn | "
        f"OCR-tekst {dbg.get('ocr_text_chars', 0)} tegn | "
        f"OCR sider {dbg.get('ocr_pages', 0)}"
    )

    questions = sorted(quiz["questions"], key=lambda x: x.get("id", 999999))

    for q in questions:
        qid = q["id"]
        st.markdown(f"### Spørgsmål {qid}")
        st.caption(q.get("context", ""))
        st.markdown(q["question"])

        st.radio(
            "Vælg svar:",
            q["options"],
            index=None,
            key=f"ans_{qid}",
        )
        st.divider()

    if st.button("Afslut quiz"):
        score = 0
        wrong = []

        for q in questions:
            sel = st.session_state.get(f"ans_{q['id']}")
            correct = q["options"][q["answer_index"]]
            if sel == correct:
                score += 1
            else:
                wrong.append((q, sel))

        st.subheader("Resultat")
        st.write(f"Score: **{score}/{len(questions)}**")

        for q, sel in wrong:
            with st.expander(f"Spørgsmål {q['id']}"):
                st.write(f"Dit svar: {sel or 'Ikke besvaret'}")
                st.write(f"Korrekt svar: {q['options'][q['answer_index']]}")
                st.write(q["explanation"])

        if score == len(questions):
            st.success("Perfekt! 🎉")
