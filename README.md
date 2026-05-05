# QuizGen

**Generering og validering af multiple choice-quizzer fra PDF-materiale med lokal LLM**

QuizGen er et lokalt webværktøj til at lave quizforslag ud fra PDF-baseret undervisningsmateriale. Systemet er udviklet til astmaundervisning for børn og unge og har fokus på dansk sprog, alderssvarende formuleringer og multiple choice-spørgsmål med præcis ét korrekt svar.

Projektet bruger en lokal LLM via Ollama, men selve quizkvaliteten styres ikke kun af modellen. Pipeline, eksempelspørgsmål, aldersprofiler, validering og lokal reparation er vigtige dele af systemet.

## Funktioner

- Upload af PDF-materiale
- Valg af hold og aldersgruppe:
  - Hold A: 6-8 år
  - Hold B: 9-12 år
  - Hold C: 13-15 år
  - Ungdom: 16-18 år
- Valg af sideinterval i PDF'en
- Generering af quizforslag med lokal Ollama-model
- Brug af eksempelspørgsmål som stil-, emne- og keyword-styring
- Validering af JSON, dansk sprog og svarmuligheder
- Krav om præcis 3 svarmuligheder og præcis 1 korrekt svar
- Det korrekte svar skal bygge på PDF-materialet
- De forkerte svar skal være plausible, men tydeligt forkerte
- Mulighed for at vælge de bedste forslag, før quizzen startes
- Frontend med farvede svarprikker til Hold A og A/B/C-svar til de øvrige hold

## Arkitektur

```text
speciale-quiz-gen/
├── app/
│   └── services/
│       ├── prompt_builder.py      # Prompts til LLM
│       ├── prompt_examples.py     # Eksempelspørgsmål og keywords
│       ├── prompt_profiles.py     # Aldersprofiler og sprogniveau
│       ├── quiz_pipeline.py       # Hovedpipeline for PDF -> quizforslag
│       └── quiz_validation.py     # Schema-, sprog- og kvalitetsvalidering
├── backend/
│   └── main.py                    # FastAPI backend
├── frontend/
│   └── index.html                 # Webinterface
├── devtools/
│   └── streamlit_app.py           # Eksperimentel/dev UI
├── requirements.txt
└── README.md
```

## Pipeline

Quizgenereringen kører overordnet sådan:

1. **Upload PDF**
   Brugeren uploader en PDF via frontend. Backend gemmer filen midlertidigt og returnerer et `upload_id`.

2. **Vælg målgruppe og sider**
   Brugeren vælger hold, antal spørgsmål og sideinterval. Holdet bestemmer aldersniveau, ordvalg og visningen i quizzen.

3. **Udtræk materiale**
   `quiz_pipeline.py` udtrækker tekst med `pdfplumber`. OCR kan bruges på billedtunge sider med `pytesseract` og `pdf2image`.

4. **Find relevante eksempler og keywords**
   `prompt_examples.py` bruges til at hente eksempelspørgsmål for det valgte hold og sideinterval. Eksemplerne hjælper modellen med stil, emner og aldersniveau.

5. **Byg prompt**
   `prompt_builder.py` samler materiale, aldersprofil, eksempler, keywords og regler til en prompt.

6. **Generér quizforslag**
   Ollama kaldes lokalt. Standardmodellen er:

   ```text
   qwen2.5:7b-instruct
   ```

7. **Normaliser og reparer**
   Outputtet parses som JSON og normaliseres. Pipeline kan rette typiske problemer i svarmuligheder, fx splittede liste-svar eller dårligt formulerede distraktorer.

8. **Valider kvalitet**
   `quiz_validation.py` kontrollerer blandt andet:
   - gyldigt JSON-schema
   - præcis 3 svarmuligheder
   - præcis 1 korrekt svar
   - korrekt svar matcher `answer_index`
   - ingen dubletter eller næsten ens spørgsmål
   - ingen engelsk/blandet sprog
   - ingen åbenlyst dårlige eller unaturlige svarmuligheder
   - genererede spørgsmål må ikke kopiere eksempelspørgsmål for tæt

9. **Vis forslag**
   Frontend viser både relevante eksempelspørgsmål og genererede spørgsmål som almindelige forslag. Brugeren vælger selv, hvilke der skal med i quizzen.

10. **Start quiz**
    De valgte spørgsmål sendes til backend, valideres igen og returneres som den endelige quiz.

## Vigtige Komponenter

### `backend/main.py`

FastAPI-backend med disse centrale endpoints:

- `GET /`  
  Server frontendens `index.html`.

- `POST /upload`  
  Uploader og gemmer en PDF.

- `POST /generate_candidates`  
  Genererer quizforslag ud fra upload, hold, antal spørgsmål og sideinterval.

- `POST /finalize_quiz`  
  Bygger den endelige quiz ud fra de spørgsmål, brugeren har valgt.

- `POST /regenerate_question`  
  Endpoint til at regenerere ét spørgsmål. Frontend bruger ikke længere denne knap i den nuværende UI.

### `app/services/quiz_pipeline.py`

Hovedlogikken for systemet. Filen håndterer:

- PDF-tekst og OCR
- kald til Ollama
- JSON parsing
- normalisering af spørgsmål
- brug af eksempelspørgsmål
- generering af ekstra spørgsmål
- reparation af svarmuligheder
- dubletfiltrering
- endelig quizvalidering

### `app/services/prompt_profiles.py`

Definerer aldersprofiler for Hold A, B, C og Ungdom. Profilerne styrer blandt andet:

- sprogniveau
- spørgsmålsstil
- længde på spørgsmål og svar
- hvor konkrete eller faglige spørgsmålene må være
- hvordan forklaringer skal skrives

### `app/services/prompt_examples.py`

Indeholder eksempelspørgsmål med sidetal, hold, svarmuligheder og korrekt svar. De bruges til:

- at give modellen en stilreference
- at styre relevante emner og keywords
- at sikre, at der kan vises gode spørgsmål, selv hvis modellen ikke laver nok ekstra forslag

### `app/services/quiz_validation.py`

Validerer quizobjekter og filtrerer dårlige spørgsmål. Den kontrollerer blandt andet schema, dubletter, svarstruktur, dansk sprog og om svarmulighederne passer til spørgsmålet.

### `frontend/index.html`

Frontend er en enkel HTML/JavaScript-app med:

- PDF-upload
- valg af hold
- valg af antal spørgsmål
- valg af sideinterval
- liste med quizforslag
- manuel udvælgelse af spørgsmål
- quizvisning med feedback
- farvede svarprikker for Hold A
- A/B/C-visning for Hold B, C og Ungdom

## Outputformat

Quizzen følger et fast JSON-format:

```json
{
  "quiz_title": "Valgt quiz",
  "questions": [
    {
      "id": 1,
      "type": "mcq",
      "question": "Hvad skal du vaske dit peakflowmeter i?",
      "options": [
        "Lunkent opvaskevand",
        "Koldt opvaskevand",
        "Varmt vand uden opvaskemiddel"
      ],
      "answer_index": 0,
      "correct_answer": "Lunkent opvaskevand",
      "explanation": "Du skal vaske dit peakflowmeter i lunkent opvaskevand.",
      "source_page": 12,
      "source_fact_id": 3,
      "topic": "Håndtering af peakflowmeter",
      "difficulty": "Let",
      "list_group": ""
    }
  ]
}
```

## Installation

### 1. Installer Python dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Installer og start Ollama

Ollama skal køre lokalt på:

```text
http://localhost:11434
```

Start Ollama:

```bash
ollama serve
```

Hent standardmodellen:

```bash
ollama pull qwen2.5:7b-instruct
```

### 3. Start backend

```bash
uvicorn backend.main:app --reload
```

### 4. Åbn frontend

Åbn:

```text
http://127.0.0.1:8000/
```

## OCR

Systemet bruger almindelig PDF-tekst, når den er god nok. OCR bruges som fallback til sider, hvor PDF'en primært består af billeder eller har for lidt tekst.

OCR kræver:

- `pytesseract`
- `pdf2image`
- Tesseract installeret lokalt
- dansk OCR-sprogdata (`dan`)

## Lokal LLM

Systemet er lavet til lokal kørsel via Ollama. Det betyder:

- materialet sendes ikke til en ekstern API
- kvalitet og hastighed afhænger af den lokale computer
- modellen kan udskiftes i `QuizGenParams` i `quiz_pipeline.py`

Standardopsætningen er optimeret til at balancere kvalitet og hastighed på en almindelig MacBook:

```python
model = "qwen2.5:7b-instruct"
temperature = 0.0
timeout = 300
num_ctx = 3072
```

## Kendte Designvalg

- Quizzen bruger altid 3 svarmuligheder.
- Der må kun være ét korrekt svar.
- Korrekt svar skal komme fra PDF-materialet.
- Forkerte svar genereres af modellen, men valideres og repareres lokalt.
- Eksempelspørgsmål må gerne vises som forslag, men genererede spørgsmål må ikke bare kopiere dem.
- Frontend viser ikke længere debug-info eller tekniske modelbeskeder til brugeren.

## Akademisk Kontekst

Projektet fungerer som softwareartefakt til et speciale om lokal LLM-baseret quizgenerering. Det kan bruges til at undersøge:

- hvordan LLM'er kan generere undervisningsspørgsmål
- hvordan validering kan øge pålideligheden
- hvordan eksempelspørgsmål kan styre sprog og emnevalg
- hvordan lokale modeller kan bruges i undervisningssystemer uden ekstern API
