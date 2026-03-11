from pathlib import Path
from uuid import uuid4
import traceback

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.services.quiz_pipeline import generate_quiz_from_pdf, QuizGenParams, OCRParams

app = FastAPI(title="QuizGen API", version="0.1")

# CORS (lokalt)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # ok lokalt; stram i prod
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = Path("backend/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


class GenerateRequest(BaseModel):
    upload_id: str
    model: str = "llama3.2:latest"
    num_questions: int = 10


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/upload")
async def upload_pdf(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Kun PDF er tilladt")

    upload_id = str(uuid4())
    save_path = UPLOAD_DIR / f"{upload_id}.pdf"

    data = await file.read()
    save_path.write_bytes(data)

    return {"upload_id": upload_id, "filename": file.filename, "bytes": len(data)}


@app.post("/generate")
def generate(req: GenerateRequest):
    pdf_path = UPLOAD_DIR / f"{req.upload_id}.pdf"
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="upload_id findes ikke (ingen PDF gemt)")

    pdf_bytes = pdf_path.read_bytes()

    # Produkt-defaults: læs "hele" PDF (højt loft) og send en fornuftig mængde tekst
    params = QuizGenParams(
        model=req.model,
        num_questions=req.num_questions,
        focus="Blandet",
        bloom="Understand",
        max_pages=120,
        max_chars=18000,
        num_ctx=2048,
        num_predict=700,
        timeout=420,
        ocr=OCRParams(use_ocr=False),
    )

    try:
        quiz, debug = generate_quiz_from_pdf(pdf_bytes, params)

        # Hvis modellen giver færre end ønsket, prøv én gang mere
        if isinstance(quiz, dict) and "questions" in quiz and len(quiz["questions"]) < req.num_questions:
            quiz2, debug2 = generate_quiz_from_pdf(pdf_bytes, params)
            if len(quiz2.get("questions", [])) > len(quiz.get("questions", [])):
                quiz, debug = quiz2, debug2

        return {
            "quiz": quiz,
            "debug": {
                "latency_s": debug.model_latency_s,
                "did_repair": debug.did_repair,
                "extracted_chars": debug.extracted_chars,
                "used_ocr": debug.used_ocr,
            },
        }

    except RuntimeError as e:
        print("\n[GENERATE RuntimeError]")
        print(str(e))
        print()
        raise HTTPException(status_code=422, detail=str(e))

    except Exception as e:
        print("\n[GENERATE Exception]")
        traceback.print_exc()
        print()
        raise HTTPException(status_code=500, detail=f"Intern fejl i generate: {type(e).__name__}: {e}")


# Server frontend på /app (IKKE /)
app.mount("/app", StaticFiles(directory="frontend", html=True), name="frontend")
