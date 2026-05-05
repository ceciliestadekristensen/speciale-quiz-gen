# app/services/prompt_profiles.py

AGE_GROUP_PROFILES = {
    "A": {
        "label": "6-8 år",
        "material_hint": "meget enkel og visuel astmaundervisning",
        "language_level": "meget enkelt dansk",
        "question_style": [
            "Brug meget korte spørgsmål",
            "Brug konkrete ord",
            "Undgå abstrakte fagord",
            "Spørg om én ting ad gangen",
            "Brug helst spørgsmål med hvad, hvor, hvornår eller hvem"
        ],
        "option_style": [
            "Svarmuligheder skal være korte",
            "Svar må ikke være for ens",
            "Forkerte svar skal være tydeligt forkerte, men stadig passe til emnet",
            "Undgå lange formuleringer"
        ],
        "explanation_style": [
            "Forklaringen skal være 1 kort sætning",
            "Brug enkelt og venligt sprog"
        ],
        "difficulty_rules": [
            "Fokusér på genkendelse frem for analyse",
            "Spørg om symptomer, hvad man skal gøre, og enkle fakta"
        ],
        "max_question_words": 12,
        "max_option_words": 6
    },
    "B": {
        "label": "9-12 år",
        "material_hint": "børnevenlig astmaundervisning",
        "language_level": "enkelt og tydeligt dansk",
        "question_style": [
            "Spørg tydeligt og konkret",
            "Brug korte sætninger",
            "Man må gerne omskrive svær tekst til lettere dansk",
            "Undgå unødigt teknisk sprog"
        ],
        "option_style": [
            "Svarmuligheder må være lidt længere end for A",
            "Forkerte svar skal være plausible men klart forkerte",
            "Undgå at bruge andre rigtige punkter fra samme liste som forkerte svar"
        ],
        "explanation_style": [
            "Forklaringen skal være 1-2 korte sætninger",
            "Forklar hvorfor det rigtige svar passer"
        ],
        "difficulty_rules": [
            "Fokusér på forståelse af enkle årsager, symptomer, medicin og hverdag",
            "Undgå meget abstrakte eller indirekte spørgsmål"
        ],
        "max_question_words": 18,
        "max_option_words": 10
    },
    "C": {
        "label": "13-15 år",
        "material_hint": "mellemtrin/teen-astmaundervisning",
        "language_level": "klart og alderssvarende dansk",
        "question_style": [
            "Spørg både om fakta og forståelse",
            "Fagord må bruges, hvis de er almindelige i materialet",
            "Undgå barnligt sprog",
            "Spørg gerne om sammenhænge, men hold det tydeligt"
        ],
        "option_style": [
            "Distraktorer må være lidt mere subtile",
            "Svar skal stadig være entydige",
            "Undgå delvist korrekte svar"
        ],
        "explanation_style": [
            "Forklaringen skal være kort og præcis",
            "Gerne 1-2 sætninger"
        ],
        "difficulty_rules": [
            "Må gerne teste forståelse af behandling, triggere, kontrol og hverdag",
            "Må gerne bruge lidt flere fagudtryk end A og B"
        ],
        "max_question_words": 22,
        "max_option_words": 14
    },
    "UNG": {
        "label": "16-18 år",
        "material_hint": "ungdomsastmaundervisning med mere fagligt niveau",
        "language_level": "modent, direkte og pædagogisk dansk",
        "question_style": [
            "Brug naturligt ungdomssprog uden at være slangagtigt",
            "Spørg om både fakta, årsag og betydning",
            "Fagord må bruges, når de findes i materialet",
            "Undgå at lyde som en quiz for små børn"
        ],
        "option_style": [
            "Svarmuligheder må være mere nuancerede",
            "Forkerte svar skal være plausible, men klart forkerte",
            "Undgå to næsten rigtige svar"
        ],
        "explanation_style": [
            "Forklar kort og fagligt korrekt",
            "Hold forklaringen klar og præcis"
        ],
        "difficulty_rules": [
            "Må gerne teste mere selvstændig forståelse",
            "Må gerne spørge til medicin, inhalationsteknik, ansvar og akut håndtering"
        ],
        "max_question_words": 26,
        "max_option_words": 18
    },
}


def get_profile(age_group: str) -> dict:
    return AGE_GROUP_PROFILES.get(age_group.upper(), AGE_GROUP_PROFILES["B"])