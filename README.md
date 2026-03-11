# QuizGen  
**Automatic Generation of Validated Multiple-Choice Quizzes from PDF Material using Local Large Language Models**

## Overview
QuizGen is a complete, end-to-end system for generating high-quality multiple-choice quizzes from PDF-based learning material.  
The system is designed as a **final product** for a master’s thesis and demonstrates how local Large Language Models (LLMs) can be integrated into a robust, validated quiz-generation pipeline.

The application extracts text from PDFs, optionally applies OCR for scanned documents, generates quiz questions using a locally hosted LLM (via Ollama), and enforces a strict JSON schema with automatic validation and repair.

## Key Capabilities
- PDF text extraction using `pdfplumber`
- Optional OCR fallback for scanned PDFs
- Quiz generation via local LLMs (Ollama `/api/chat`)
- Strict schema validation for MCQ quizzes
- Automatic repair pass if the model returns invalid JSON
- REST API for quiz generation
- Simple web-based user interface for demonstration and evaluation

## Architecture
The system is intentionally modular and UI-agnostic at its core.

SPECIALE-QUIZ-GEN/
├── app/
│ └── services/
│ └── quiz_pipeline.py # Core quiz generation logic
├── backend/
│ ├── main.py # FastAPI application
│ └── uploads/ # Uploaded PDFs
├── frontend/
│ └── index.html # Demo web interface
├── README.md
└── .venv/

### Core Components
- **quiz_pipeline.py**  
  Implements the full quiz generation pipeline:
  - PDF text extraction
  - OCR fallback (optional)
  - Prompt construction
  - LLM invocation via Ollama
  - JSON parsing, normalization, validation, and repair

- **FastAPI Backend (`backend/main.py`)**  
  Exposes the pipeline as a REST API:
  - `/upload` – upload PDF material
  - `/generate` – generate quiz from uploaded PDF
  - `/health` – service health check

- **Frontend (`frontend/index.html`)**  
  Lightweight HTML/JavaScript interface used for demonstration and evaluation.  
  Served directly by the FastAPI backend at `/app`.

## Design Rationale
This system is **not implemented in Streamlit** by design.  
The separation between:
- core logic (quiz generation),
- backend API,
- and frontend UI

reflects a production-oriented architecture rather than an exploratory prototype.  
This allows the quiz generation pipeline to be reused across different interfaces (web, CLI, LMS integration) without modification.

## Requirements
- Python 3.11+
- Ollama (local installation)
- At least one Ollama model installed (e.g. `llama3.2`)

Python dependencies include:
- fastapi
- uvicorn
- pdfplumber
- requests
- pydantic
- pytesseract (optional, for OCR)
- pdf2image (optional, for OCR)

## Running the System Locally

### 1. Start Ollama
Ensure Ollama is running locally:

```bash
ollama serve
```
Pull a model if needed: 

ollama pull llama3.2

### 2. Set up Python environment
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

### 3. Start the backend
uvicorn backend.main:app --reload --port 8000

### 4. Open the web interface
Open in a browser: http://localhost:8000/app

**Quiz Generation Workflow**
- User uploads a PDF
- Text is extracted (OCR applied if enabled and needed)
- Material is truncated to a configurable size
- LLM generates quiz questions in JSON format
- Output is normalized and validated
- If validation fails, a repair prompt is applied
- Final validated quiz is returned to the user

**Output Format**
The generated quiz follows a strict schema:

{
  "quiz_title": "string",
  "questions": [
    {
      "id": 1,
      "type": "mcq",
      "context": "string",
      "question": "string",
      "options": ["A", "B", "C", "D"],
      "answer_index": 0,
      "explanation": "string"
    }
  ]
}

#### Academic Context 
This system constitutes the final software artifact for a master’s thesis.
It is intended to support analysis and discussion of:

- automated assessment generation
- reliability of LLM outputs
- schema enforcement and repair strategies
- use of local LLMs in educational systems
