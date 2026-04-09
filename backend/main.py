from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.services.quiz_pipeline import (
    OCRParams,
    QuizGenParams,
    finalize_selected_questions,
    generate_quiz_candidates_from_pdf,
    regenerate_single_question_from_pdf,
)

BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_DIR = BASE_DIR / "backend" / "uploads"
FRONTEND_DIR = BASE_DIR / "frontend"

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="QuizGen API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def serve_index():
    index_path = FRONTEND_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="frontend/index.html ikke fundet")
    return FileResponse(index_path)


class GenerateRequest(BaseModel):
    upload_id: str
    num_questions: int = Field(default=10, ge=3, le=30)
    age_group: str = "B"
    page_from: int | None = None
    page_to: int | None = None


class FinalizeRequest(BaseModel):
    quiz_title: str | None = None
    selected_questions: list[dict]


class RegenerateQuestionRequest(BaseModel):
    upload_id: str
    age_group: str = "B"
    page_from: int | None = None
    page_to: int | None = None
    old_question: dict
    existing_questions: list[dict] = Field(default_factory=list)


def validate_common_inputs(upload_id: str, age_group: str, page_from: int | None, page_to: int | None) -> Path:
    pdf_path = UPLOAD_DIR / f"{upload_id}.pdf"
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="Upload ikke fundet")

    if page_from is not None and page_from < 1:
        raise HTTPException(status_code=400, detail="Fra side skal være mindst 1")

    if page_to is not None and page_to < 1:
        raise HTTPException(status_code=400, detail="Til side skal være mindst 1")

    if page_from is not None and page_to is not None and page_from > page_to:
        raise HTTPException(status_code=400, detail="Fra side må ikke være større end til side")

    allowed_groups = {"A", "B", "C", "UNG"}
    if age_group not in allowed_groups:
        raise HTTPException(status_code=400, detail="Hold skal være A, B, C eller UNG")

    return pdf_path


@app.post("/upload")
async def upload_pdf(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filnavn mangler")

    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Kun PDF-filer er tilladt")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Filen er tom")

    upload_id = str(uuid4())
    save_path = UPLOAD_DIR / f"{upload_id}.pdf"

    with open(save_path, "wb") as f:
        f.write(content)

    return {
        "upload_id": upload_id,
        "filename": file.filename,
        "bytes": len(content),
    }


@app.post("/generate_candidates")
def generate_candidates(req: GenerateRequest):
    pdf_path = validate_common_inputs(
        upload_id=req.upload_id,
        age_group=req.age_group,
        page_from=req.page_from,
        page_to=req.page_to,
    )

    pdf_bytes = pdf_path.read_bytes()

    try:
        candidates, debug = generate_quiz_candidates_from_pdf(
            pdf_bytes=pdf_bytes,
            params=QuizGenParams(
                num_questions=req.num_questions,
                age_group=req.age_group,
                page_from=req.page_from,
                page_to=req.page_to,
                ocr=OCRParams(
                    use_ocr=True,
                    max_pages=12,
                    dpi=160,
                    lang="eng",
                ),
            ),
        )

        return {
            "quiz": candidates,
            "debug": {
                "latency_s": debug.model_latency_s,
                "did_repair": debug.did_repair,
                "used_ocr": debug.used_ocr,
                "extracted_chars": debug.extracted_chars,
                "page_range": debug.page_range,
            },
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/regenerate_question")
def regenerate_question(req: RegenerateQuestionRequest):
    pdf_path = validate_common_inputs(
        upload_id=req.upload_id,
        age_group=req.age_group,
        page_from=req.page_from,
        page_to=req.page_to,
    )

    pdf_bytes = pdf_path.read_bytes()

    try:
        new_question = regenerate_single_question_from_pdf(
            pdf_bytes=pdf_bytes,
            params=QuizGenParams(
                num_questions=1,
                age_group=req.age_group,
                page_from=req.page_from,
                page_to=req.page_to,
                ocr=OCRParams(
                    use_ocr=True,
                    max_pages=12,
                    dpi=160,
                    lang="eng",
                ),
            ),
            old_question=req.old_question,
            existing_questions=req.existing_questions,
        )

        return {"question": new_question}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/finalize_quiz")
def finalize_quiz(req: FinalizeRequest):
    if not req.selected_questions:
        raise HTTPException(status_code=400, detail="Du skal vælge mindst ét spørgsmål")

    try:
        quiz = finalize_selected_questions(
            selected_questions=req.selected_questions,
            quiz_title=req.quiz_title or "Valgt quiz",
        )
        return {"quiz": quiz}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))