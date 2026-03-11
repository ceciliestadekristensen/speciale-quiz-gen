from pathlib import Path
from app.services.quiz_pipeline import generate_quiz_from_pdf, QuizGenParams, OCRParams

pdf_bytes = Path("app/services/Scrum.pdf").read_bytes()

params = QuizGenParams(
    model="llama3.2:latest",
    num_questions=2,
    focus="Blandet",
    bloom="Understand",
    max_pages=2,
    max_chars=1200,
    temperature=0.2,
    timeout=120,
    num_ctx=1024,
    num_predict=120,
    ocr=OCRParams(use_ocr=False),
)

quiz, debug = generate_quiz_from_pdf(pdf_bytes, params)

print("TITLE:", quiz.get("quiz_title"))
print("QUESTIONS:", len(quiz["questions"]))
print("REPAIR:", debug.did_repair)
print("LATENCY:", debug.model_latency_s)
