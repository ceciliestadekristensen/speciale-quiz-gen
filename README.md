# QuizGen

**Generering og validering af multiple choice-quizzer fra PDF-materiale med lokal LLM**

QuizGen er et lokalt webværktøj, der laver quizforslag ud fra PDF-baseret undervisningsmateriale. Systemet er udviklet i samarbejde med **Astmaskolen for Børn og Unge** (Børne- og Ungeafdelingen, Aalborg Universitetshospital) og har fokus på dansk sprog, alderssvarende formuleringer og multiple choice-spørgsmål med præcis ét korrekt svar.

Projektet startede som softwareartefakt til mit speciale (cand.it., Digitalisering og Applikationsudvikling, AAU, 2026). Efter specialet har jeg videreudviklet det og overleveret det til Astmaskolen med installationspakke og vejledning, så det kan bruges uden teknisk erfaring.

![QuizGen demo](https://ceciliestadekristensen.github.io/Portfolio/demo/quizgen.gif)

Se hele projektbeskrivelsen i mit [portfolio](https://ceciliestadekristensen.github.io/Portfolio/projects/quizgen.html).

## Hvorfor lokal LLM?

Astmaskolen er en del af et hospital og ønsker ingen løbende udgifter til hosting eller betalte AI-tjenester. Derfor kører hele systemet lokalt på brugerens egen computer via Ollama:

- Materialet sendes ikke til en ekstern API eller cloud-tjeneste
- Der er ingen løbende udgifter
- Det er lettere for regionens IT at godkende, fordi der ikke indgår eksterne udbydere

Prisen er, at kvalitet og hastighed afhænger af den lokale computer. Derfor styres quizkvaliteten ikke kun af modellen: pipeline, eksempelspørgsmål, aldersprofiler, validering og lokal reparation er centrale dele af systemet.

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
- Læreren vælger de bedste forslag, før quizzen startes (teacher-in-the-loop)
- Læreren kan rette genererede spørgsmål og tilføje sine egne
- Farvede svarprikker for alle hold, så svarmulighederne er nemme at pege på i klassen
- OCR-fallback til billedtunge sider
- Installation og start med ét klik på Windows og Mac

## Kom i gang (brugere)

Se [`LÆSMIG.txt`](LÆSMIG.txt) for den fulde vejledning. Kort fortalt:

1. Installer [Python 3.10+](https://www.python.org/), [Ollama](https://ollama.com/) og Tesseract OCR med dansk sprogpakke.
2. Kør installeren én gang:
   - **Windows:** dobbeltklik på `installer.bat`
   - **Mac:** dobbeltklik på `installer.command`
3. Start QuizGen med genvejen på skrivebordet (`start.bat` / `start.command`). Browseren åbner automatisk.

Installeren opretter et virtuelt Python-miljø, installerer afhængigheder, henter sprogmodellen (ca. 5 GB) og laver en genvej på skrivebordet.

## Kom i gang (udvikling)

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

ollama pull qwen2.5:7b-instruct
ollama serve                     # hvis Ollama ikke allerede kører

uvicorn backend.main:app --reload
```

Åbn derefter `http://127.0.0.1:8000/`.

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
│   └── main.py                    # FastAPI-backend
├── frontend/
│   └── index.html                 # Webinterface
├── installer.bat / installer.command   # Installation (Windows / Mac)
├── start.bat / start.command           # Start med ét klik
├── LÆSMIG.txt                     # Brugervejledning
├── requirements.txt
└── README.md
```

## Skærmbilleder

| Generering | Quizforslag |
|---|---|
| ![Generering af quizforslag](https://ceciliestadekristensen.github.io/Portfolio/images/quiz-hold-a-generering.png) | ![Quizforslag](https://ceciliestadekristensen.github.io/Portfolio/images/quiz-candidates.png) |

| Tilføj eget spørgsmål | Færdig quiz |
|---|---|
| ![Tilføj eget spørgsmål](https://ceciliestadekristensen.github.io/Portfolio/images/quiz-tilfoej-spoergsmaal.png) | ![Færdig quiz](https://ceciliestadekristensen.github.io/Portfolio/images/quiz-final.png) |

## Pipeline

1. **Upload PDF**
   Brugeren uploader en PDF via frontend. Backend gemmer filen midlertidigt og returnerer et `upload_id`. Uploads slettes automatisk efter et døgn.

2. **Vælg målgruppe og sider**
   Brugeren vælger hold, antal spørgsmål og sideinterval. Holdet bestemmer aldersniveau, ordvalg og visningen i quizzen.

3. **Udtræk materiale**
   Tekst udtrækkes med `pdfplumber`. Sider med for lidt tekst renderes til billeder og læses med Tesseract OCR.

4. **Find relevante eksempler og keywords**
   `prompt_examples.py` henter eksempelspørgsmål for det valgte hold og sideinterval. Eksemplerne hjælper modellen med stil, emner og aldersniveau.

5. **Byg prompt**
   `prompt_builder.py` samler materiale, aldersprofil, eksempler, keywords og regler til en prompt.

6. **Generér quizforslag**
   Ollama kaldes lokalt med `qwen2.5:7b-instruct`.

7. **Normaliser og reparer**
   Outputtet parses som JSON og normaliseres. Pipelinen retter typiske problemer i svarmuligheder, fx splittede liste-svar eller dårligt formulerede distraktorer.

8. **Valider kvalitet**
   `quiz_validation.py` kontrollerer blandt andet:
   - gyldigt JSON-schema
   - præcis 3 svarmuligheder og præcis 1 korrekt svar
   - at det korrekte svar matcher `answer_index`
   - ingen dubletter eller næsten ens spørgsmål
   - ingen engelsk eller blandet sprog
   - ingen åbenlyst dårlige eller unaturlige svarmuligheder
   - at genererede spørgsmål ikke kopierer eksempelspørgsmål for tæt

9. **Vis forslag**
   Frontend viser relevante eksempelspørgsmål og genererede spørgsmål som forslag. Læreren kan rette forslagene, tilføje egne spørgsmål og vælge, hvilke der skal med.

10. **Start quiz**
    De valgte og evt. redigerede spørgsmål valideres igen i backend og returneres som den endelige quiz.

## API

| Metode | Endpoint | Beskrivelse |
|---|---|---|
| `GET` | `/` | Serverer frontendens `index.html` |
| `POST` | `/upload` | Uploader og gemmer en PDF midlertidigt |
| `POST` | `/generate_candidates` | Genererer quizforslag ud fra upload, hold, antal og sideinterval |
| `POST` | `/finalize_quiz` | Bygger den endelige quiz ud fra de valgte spørgsmål |
| `POST` | `/regenerate_question` | Regenererer ét spørgsmål (bruges ikke i den nuværende UI) |

## Outputformat

```json
{
  "quiz_title": "Valgt quiz",
  "questions": [
    {
      "id": 1,
      "type": "mcq",
      "question": "Eksempel på et genereret spørgsmål",
      "options": ["Svar A", "Svar B", "Svar C"],
      "answer_index": 0,
      "correct_answer": "Svar A",
      "explanation": "Kort forklaring på det korrekte svar.",
      "source_page": 1,
      "source_fact_id": 1,
      "topic": "Eksempel",
      "difficulty": "Let",
      "list_group": ""
    }
  ]
}
```

## Konfiguration

Standardopsætningen balancerer kvalitet og hastighed på en almindelig bærbar:

```python
model = "qwen2.5:7b-instruct"
temperature = 0.0
timeout = 300
num_ctx = 3072
```

Modellen kan udskiftes i `QuizGenParams` i `quiz_pipeline.py`. Stien til Tesseract kan sættes med miljøvariablen `TESSERACT_CMD`, hvis den ikke findes automatisk.

## Sikkerhed og drift

- Backend lytter kun på `127.0.0.1` og er ikke tilgængelig fra netværket
- Frontend serveres fra samme adresse, så der er ingen åben CORS-konfiguration
- Uploadede PDF'er slettes automatisk efter 24 timer
- Ingen data forlader computeren efter installation

## Kendte designvalg

- Quizzen bruger altid 3 svarmuligheder med ét korrekt svar.
- Det korrekte svar skal komme fra PDF-materialet.
- Forkerte svar genereres af modellen, men valideres og repareres lokalt.
- Eksempelspørgsmål (skrevet af mig ud fra Astmaskolens materiale) må gerne vises som forslag, men genererede spørgsmål må ikke kopiere dem.
- Frontend viser ikke debug-info eller tekniske modelbeskeder til brugeren.

## Akademisk kontekst

Projektet er udviklet som softwareartefakt til et speciale om lokal LLM-baseret quizgenerering og undersøger:

- hvordan LLM'er kan generere undervisningsspørgsmål til børn og unge
- hvordan validering kan øge pålideligheden af LLM-output
- hvordan eksempelspørgsmål kan styre sprog og emnevalg
- hvordan lokale modeller kan bruges i undervisningssystemer uden ekstern API

## Kontakt

Cecilie Städe Kristensen · [ceciliestade@gmail.com](mailto:ceciliestade@gmail.com) · [LinkedIn](https://www.linkedin.com/in/cecilie-stade-880457232/) · [Portfolio](https://ceciliestadekristensen.github.io/Portfolio/index.html)
