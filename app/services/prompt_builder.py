# app/services/prompt_builder.py

import json
from app.services.prompt_examples import get_examples_for_page_range, get_keywords_for_page_range
from app.services.prompt_profiles import get_profile


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def _style_examples(age_group: str, page_from: int | None = None, page_to: int | None = None) -> str:
    lines = []
    examples = get_examples_for_page_range(age_group, page_from, page_to)
    if not examples:
        return "Ingen faste eksempler for det valgte sideinterval. Følg stadig aldersniveauet."

    for idx, example in enumerate(examples, start=1):
        options = " | ".join(example["options"])
        lines.append(
            f'{idx}. Side {example["source_page"]}: "{example["question"]}" '
            f'Svarmuligheder: {options}. Korrekt: {example["correct_answer"]}'
        )
    return "\n".join(lines)


def _short_style_examples(
    age_group: str,
    page_from: int | None = None,
    page_to: int | None = None,
    limit: int = 4,
) -> str:
    lines = []
    examples = get_examples_for_page_range(age_group, page_from, page_to)[:limit]
    if not examples:
        return "Ingen faste eksempler for det valgte sideinterval."

    for idx, example in enumerate(examples, start=1):
        options = " / ".join(str(option) for option in example["options"][:3])
        lines.append(
            f'{idx}. "{example["question"]}" | {options} | korrekt: {example["correct_answer"]}'
        )
    return "\n".join(lines)


def _compact_style_examples(age_group: str, page_from: int | None = None, page_to: int | None = None) -> str:
    lines = []
    examples = get_examples_for_page_range(age_group, page_from, page_to)
    if not examples:
        return "Ingen faste eksempler for det valgte sideinterval."

    for idx, example in enumerate(examples, start=1):
        lines.append(
            f'{idx}. Side {example["source_page"]}: emne/svar: {example["correct_answer"]}'
        )
    return "\n".join(lines)


def _keywords(age_group: str, page_from: int | None = None, page_to: int | None = None) -> str:
    keywords = get_keywords_for_page_range(age_group, page_from, page_to)
    if not keywords:
        return "Ingen nøgleord fra eksempler for det valgte sideinterval."
    return ", ".join(keywords)


def build_fact_extraction_prompt(material: str, params, max_facts: int = 18) -> str:
    profile = get_profile(params.age_group)
    min_facts = min(max(5, getattr(params, "num_questions", 5)), max_facts)

    return f"""
Du er underviser og faglig redaktør.

Skriv ALT på dansk.
Returnér KUN gyldig JSON.

Målgruppe:
- Hold: {params.age_group}
- Alder: {profile["label"]}
- Niveau: {profile["language_level"]}

Opgave:
- Udtræk {min_facts}-{max_facts} korte facts, som kan bruges til quizspørgsmål.
- Brug kun oplysninger fra materialet.
- Returnér kun JSON.

Regler:
- Brug kun oplysninger, som står i materialet eller tydeligt kan udledes direkte af det.
- Fakta skal være korte og konkrete.
- Vælg facts med variation fra forskellige emner.
- Undgå dubletter.
- correct_answer skal være et konkret svar fra materialet.
- Hvis et fact kommer fra en liste, skal list_group være listens emne, fx "tegn på astma".
- Hvis et fact ikke kommer fra en liste, skal list_group være "".
- topic skal være kort, fx "symptomer", "medicin", "peakflowmeter" eller "rengøring".

Schema:
{{
  "facts": [
    {{
      "fact_id": 1,
      "topic": "symptomer",
      "list_group": "tegn på astma",
      "source_page": 4,
      "fact": "Hoste kan være et tegn på astma.",
      "correct_answer": "Hoste",
      "importance": "high"
    }}
  ]
}}

Materiale:
\"\"\"
{material}
\"\"\"
""".strip()


def build_fact_repair_prompt(model_output: str, max_facts: int = 18) -> str:
    min_facts = min(8, max_facts)

    return f"""
Du skal rette outputtet til gyldig JSON.

Returnér KUN ét JSON-objekt.
Ingen markdown.
Ingen tekst før eller efter JSON.
Skriv ALT på dansk.

JSON skal følge dette schema:
{{
  "facts": [
    {{
      "fact_id": 1,
      "topic": "kort emne",
      "list_group": "",
      "source_page": 0,
      "fact": "kort faktum fra materialet",
      "correct_answer": "kort korrekt svar",
      "importance": "high"
    }}
  ]
}}

Regler:
- Der skal være mellem {min_facts} og {max_facts} facts, hvis outputtet indeholder nok information.
- Behold kun facts, der allerede fremgår af outputtet.
- Opfind ikke nye fakta.
- correct_answer skal være kort.
- importance skal være "low", "medium" eller "high".

Output der skal rettes:
{model_output}
""".strip()


def build_question_generation_prompt(material: str, facts_obj: dict, params, n_candidates: int) -> str:
    profile = get_profile(params.age_group)
    facts_json = json.dumps(facts_obj, ensure_ascii=False, indent=2)
    examples = _short_style_examples(params.age_group, params.page_from, params.page_to)
    keywords = _keywords(params.age_group, params.page_from, params.page_to)

    return f"""
Du er en dygtig quizforfatter for børn og unge.

Skriv ALT på dansk.
Returnér KUN gyldig JSON.

Målgruppe:
- Hold: {params.age_group}
- Alder: {profile["label"]}
- Niveau: {profile["language_level"]}
- Materialetype: {profile["material_hint"]}

Krav til spørgsmålsstil:
{_bullets(profile["question_style"])}

Krav til svarmuligheder:
{_bullets(profile["option_style"])}

Krav til forklaringer:
{_bullets(profile["explanation_style"])}

Sværhedsregler:
{_bullets(profile["difficulty_rules"])}

Stil-eksempler for denne aldersgruppe og de valgte sider:
{examples}

Nøgleord fra eksemplerne i det valgte sideinterval:
{keywords}

Sådan bruger du eksemplerne:
- Brug dem kun til at forstå emner, ordvalg og aldersniveau.
- Kopiér ikke eksempelspørgsmålene.
- Lav ikke rene omformuleringer af eksempelspørgsmålene.
- Bland ikke dele fra flere eksempler sammen til et nyt spørgsmål.
- Brug aldrig en forkert svarmulighed fra eksemplerne som korrekt svar.
- Brug kun nøgleord, hvis de også passer med materialet nedenfor.

Opgave:
- Lav præcis {n_candidates} multiple choice-spørgsmål.
- Hvert spørgsmål skal have præcis 3 svarmuligheder.
- Hvert spørgsmål skal bruge én valgt fact fra facts-listen.
- Det korrekte svar skal være præcis samme tekst som correct_answer fra den valgte fact.
- De to forkerte svar skal du selv opfinde.
- Der må kun være ét korrekt svar.

Regler for forkerte svar:
- Forkerte svar må ikke være correct_answer fra andre facts.
- Forkerte svar må ikke være andre korrekte oplysninger fra materialet.
- Hvis den valgte fact har list_group, må forkerte svar ikke være correct_answer fra samme list_group.
- Forkerte svar skal være plausible i emnet, men klart forkerte.
- Brug aldrig "Alle ovenstående", "Ingen af ovenstående" eller "Ved ikke".

Regler for indhold:
- Brug kun facts-listen nedenfor som kilde til spørgsmål og rigtige svar.
- Spørgsmålene må ikke være næsten ens.
- Spørgsmålene skal dække forskellige emner.
- Brug forskellige source_fact_id til spørgsmålene.
- Lav ikke to spørgsmål om samme emne, samme fact eller samme pointe.

Regler for sprog:
- Sproget skal passe tydeligt til målgruppen.
- Et spørgsmål må højst være cirka {profile["max_question_words"]} ord.
- En svarmulighed må højst være cirka {profile["max_option_words"]} ord.
- Spørgsmålet skal være naturligt dansk og kunne læses højt for børn.
- Skriv spørgsmål som en dansk underviser ville sige dem højt.
- Brug ikke engelske fyldord eller maskinoversatte ord.
- Fagord fra PDF'en må bruges, fx peakflowmeter, hvis det er det ord materialet bruger.
- Brug aldrig ordet "astmatak". Brug "astmaanfald", hvis det står i facts-listen.
- Undgå maskinoversat dansk, fx "Hvad er en mål for...", "Hvad kan føre til at du skal bruge..." og "daglig rutine for".

Hvert spørgsmål skal også have intern metadata:
- source_page
- source_fact_id
- topic
- list_group
- difficulty
- correct_answer

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
      "correct_answer": "det korrekte svar fra den valgte fact",
      "explanation": "kort forklaring på dansk",
      "source_page": 4,
      "source_fact_id": 1,
      "topic": "symptomer",
      "list_group": "tegn på astma",
      "difficulty": "easy"
    }}
  ]
}}

Facts:
{facts_json}
""".strip()


def build_direct_question_generation_prompt(material: str, params, n_candidates: int) -> str:
    profile = get_profile(params.age_group)
    examples = _short_style_examples(params.age_group, params.page_from, params.page_to)
    keywords = _keywords(params.age_group, params.page_from, params.page_to)

    return f"""
Du er en dygtig dansk quizforfatter for børn og unge.

Returnér KUN gyldig JSON.
Skriv ALT på dansk.

Målgruppe:
- Hold: {params.age_group}
- Alder: {profile["label"]}
- Niveau: {profile["language_level"]}

Stil-eksempler for denne aldersgruppe og de valgte sider:
{examples}

Nøgleord fra eksemplerne i det valgte sideinterval:
{keywords}

Sådan bruger du eksemplerne:
- Brug dem kun til at forstå emner, ordvalg og aldersniveau.
- Kopiér ikke eksempelspørgsmålene.
- Lav ikke rene omformuleringer af eksempelspørgsmålene.
- Brug kun nøgleord, hvis de også passer med materialet nedenfor.

Opgave:
- Lav præcis {n_candidates} multiple choice-spørgsmål ud fra materialet.
- Hvert spørgsmål skal have præcis 3 svarmuligheder.
- Der må kun være 1 korrekt svar.
- Det korrekte svar skal stå i materialet eller være en kort, direkte omskrivning af materialet.
- De 2 forkerte svar skal du selv opfinde.
- Det korrekte svar må ikke være taget fra en forkert svarmulighed i eksemplerne.

Regler for kvalitet:
- Spørg kun om indhold, der står i de valgte sider i materialet.
- source_page skal være en af de [Side X]-markeringer, der findes i materialet.
- Spørgsmålene skal være naturligt dansk og kunne læses højt for børn.
- Spørg om én tydelig ting ad gangen.
- Spørgsmålene må ikke være næsten ens.
- Brug forskellige emner fra materialet.
- For Hold A og B skal spørgsmål handle om simple, konkrete ting barnet kan genkende eller gøre.
- For Hold A og B må du ikke spørge om lungeanatomi, sygdomstyper, alveoler, alveolitis, inflammation, slimhinder eller muskler i luftvejene.
- For Hold C må du ikke spørge om alveolitis.
- Fagord fra PDF'en må bruges, fx peakflowmeter, hvis det er ordet i materialet.
- Brug ikke engelske fyldord eller maskinoversatte formuleringer.
- Undgå "Hvilken af følgende...", "Hvad er en mål for..." og "Hvad kan føre til at du skal bruge...".

Regler for svarmuligheder:
- Forkerte svar må ikke være andre korrekte oplysninger fra materialet.
- Hvis materialet har en liste, må kun ét punkt fra listen være med som korrekt svar.
- De to forkerte svar skal være plausible, men klart forkerte.
- De to forkerte svar skal være naturlige danske svar, ikke mærkelige maskinoversatte sætninger.
- Lav ikke brede spørgsmål, hvis flere svarmuligheder kan være rigtige.
- Lav ikke spørgsmål hvor flere svarmuligheder er samme type korrekt ting, fx flere symptomer eller flere former for motion.
- Brug aldrig "Alle ovenstående", "Ingen af ovenstående" eller "Ved ikke".
- correct_answer skal være præcis samme tekst som den korrekte svarmulighed.

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
      "correct_answer": "svar 1",
      "explanation": "kort forklaring på dansk",
      "source_page": 4,
      "source_fact_id": 1,
      "topic": "kort emne",
      "list_group": "",
      "difficulty": "easy"
    }}
  ]
}}

Materiale:
\"\"\"
{material}
\"\"\"
""".strip()


def build_direct_supplement_prompt(
    material: str,
    params,
    missing_count: int,
    existing_questions: list[dict],
    rejection_notes: list[str] | None = None,
) -> str:
    profile = get_profile(params.age_group)
    existing_summary = [str(question.get("question", "")) for question in existing_questions]
    used_answers = [
        str(question.get("correct_answer", "")).strip()
        for question in existing_questions
        if str(question.get("correct_answer", "")).strip()
    ]
    used_topics = [
        str(question.get("topic", "")).strip()
        for question in existing_questions
        if str(question.get("topic", "")).strip()
    ]
    existing_json = json.dumps(existing_summary, ensure_ascii=False, indent=2)
    used_answers_json = json.dumps(used_answers, ensure_ascii=False, indent=2)
    used_topics_json = json.dumps(sorted(set(used_topics)), ensure_ascii=False, indent=2)
    rejection_notes_json = json.dumps(rejection_notes or [], ensure_ascii=False, indent=2)
    examples = _short_style_examples(params.age_group, params.page_from, params.page_to)
    keywords = _keywords(params.age_group, params.page_from, params.page_to)

    return f"""
Returnér KUN gyldig JSON.
Skriv ALT på dansk.

Målgruppe:
- Hold: {params.age_group}
- Alder: {profile["label"]}
- Niveau: {profile["language_level"]}

Opgave:
- Lav præcis {missing_count} NYE multiple choice-spørgsmål ud fra materialet.
- Hvert spørgsmål skal have præcis 3 svarmuligheder.
- Der må kun være 1 korrekt svar.

Eksempelspørgsmål håndteres allerede af koden og er allerede med som forslag.
Du skal KUN lave de manglende nye spørgsmål.
Brug eksemplerne herunder som kvalitetsrubrik for gode spørgsmål til netop dette hold.
De viser emner, ordvalg, længde, spørgsmålstype og hvordan svarmuligheder skal hænge sammen.
Kopiér dem ikke, og lav ikke rene omformuleringer.
Gode eksempler for dette hold og sideinterval:
{examples}

Nøgleord:
{keywords}

FORBUDTE formuleringer. De nye spørgsmål må ikke ligne disse:
{existing_json}

Korrekte svar der allerede er brugt. Brug ikke disse som korrekt svar igen:
{used_answers_json}

Emner der allerede er dækket. Vælg en anden pointe fra materialet, hvis det er muligt:
{used_topics_json}

Tidligere afviste forsøg i denne generering. Lav ikke samme fejl igen:
{rejection_notes_json}

Regler:
- Brug kun materialet nedenfor og vælg emner, der passer til eksemplerne/nøgleordene.
- Korrekt svar skal være et fuldt, naturligt og fagligt rigtigt svar fra materialet.
- Bevar præcise ord fra materialet, fx temperatur, tid, farve, tal og mængde. Byt ikke til et lignende ord.
- Bevar mådesudsagnsord fra materialet. Byt ikke "skal", "kan", "må" eller "bør" ud med hinanden.
- De to forkerte svar skal du selv opfinde. De må ikke være andre korrekte facts fra materialet.
- Ved liste-spørgsmål, fx symptomer eller tegn, må de forkerte svar ikke være andre rigtige punkter fra samme liste.
- Brug ikke brede symptomspørgsmål som "Hvad kan du opleve..."; de giver ofte flere rigtige svar.
- Alle tre svarmuligheder skal have samme svarform, fx tre handlinger, tre tidspunkter eller tre farver.
- Svarformen skal passe til spørgsmålet: spørg kun med farver, hvis spørgsmålet spørger efter farve/zone.
- Hvis spørgsmålet spørger hvad man skal gøre, skal alle svar være handlinger. Hvis spørgsmålet spørger hvornår, skal alle svar være tidspunkter/situationer.
- Hvis svarene er handlinger, så spørg "Hvad skal du gøre..." - ikke "Hvad sker der...".
- Forkerte handlingssvar skal være tæt på samme beslutning som det korrekte svar, men tydeligt forkerte.
- Hvis det korrekte svar handler om at skrive/notere/registrere noget, skal de forkerte svar også handle om forkert notering eller hukommelse, ikke om medicin, vask eller udstyr.
- Hvis det korrekte svar handler om hvornår noget skal vaskes, skal svarene være tre forskellige tidspunkter/frekvenser. Bevar præcis temperatur og hyppighed fra materialet i det korrekte svar.
- Hvis det korrekte svar er et konkret trin med vigtige detaljer, skal spørgsmålet nævne de vigtigste detaljer. Spørg ikke bredt om et smalt trin.
- Brug ikke "kun", hvis materialet siger "ofte" eller "kan".
- Brug naturligt dansk til alderen. Ingen engelske ord, opfundne ord eller halve handlinger.
- Kopiér ikke eksemplerne og lav ikke rene omformuleringer.
- Brug helst "Hvad" eller "Hvordan" ved handlinger/procedurer. Brug kun "Hvornår" ved rigtige tidspunkter/situationer.
- For Hold A/B: ingen alveoler, alveolitis, inflammation eller svær lungeanatomi.
- Skriv ikke A), B) eller C) inde i svarteksterne.

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
      "correct_answer": "svar 1",
      "explanation": "kort forklaring på dansk",
      "source_page": 4,
      "source_fact_id": 1,
      "topic": "kort emne",
      "list_group": "",
      "difficulty": "easy"
    }}
  ]
}}

Materiale:
\"\"\"
{material}
\"\"\"
""".strip()


def build_guided_single_supplement_prompt(
    focus_point: str,
    params,
    existing_questions: list[dict],
    rejection_notes: list[str] | None = None,
    question_count: int = 1,
) -> str:
    profile = get_profile(params.age_group)
    examples = _short_style_examples(params.age_group, params.page_from, params.page_to, limit=2)
    existing_summary = [
        str(question.get("question", "")).strip()
        for question in existing_questions
        if str(question.get("question", "")).strip()
    ][:6]
    used_answers = [
        str(question.get("correct_answer", "")).strip()
        for question in existing_questions
        if str(question.get("correct_answer", "")).strip()
    ][:8]

    return f"""
Kun gyldig JSON. Dansk.
Målgruppe: Hold {params.age_group}, {profile["label"]}. Niveau: {profile["language_level"]}.

Gode eksempler på stil:
{examples}

PDF-punkter du må bruge:
{focus_point}

Lav præcis {question_count} NYE quizspørgsmål.

Undgå disse spørgsmål:
{json.dumps(existing_summary, ensure_ascii=False)}

Brug ikke disse som korrekt svar igen:
{json.dumps(used_answers, ensure_ascii=False)}

Regler:
- Ét PDF-punkt per spørgsmål.
- Korrekt svar skal stå i PDF-punktet. Byt ikke tal, tid, farve, temperatur eller "skal/kan".
- Lav 2 forkerte svar selv. De skal have samme svarform som det korrekte svar.
- Hvis PDF-punktet har flere korrekte punkter, må de ikke bruges som 3 separate svarmuligheder. Saml dem i ét korrekt svar, eller vælg et andet spørgsmål.
- Ved hvorfor-spørgsmål må forkerte svar ikke være samme årsag med andre ord.
- Forkerte svar skal være tæt på samme lille handling/valg, ikke tilfældige steder, udstyr eller emballage.
- Kun 1 svar må være korrekt.
- Kopiér ikke eksemplerne og lav ikke næsten samme spørgsmål.
- Skriv naturligt dansk til målgruppen. Brug kun danske ord, bortset fra fagord der står i PDF-punktet.
- Svarmuligheder må ikke blande dansk og engelsk.
- Lav rigtige spørgsmål, ikke bare PDF-sætninger med spørgsmålstegn.
- Ingen engelske fyldord, opfundne ord eller halve handlinger.

Schema:
{{"quiz_title":"Spørgsmålsforslag","questions":[{{"id":1,"type":"mcq","question":"spørgsmål?","options":["svar 1","svar 2","svar 3"],"answer_index":0,"correct_answer":"svar 1","explanation":"kort forklaring","source_page":0,"source_fact_id":1,"topic":"kort emne","list_group":"","difficulty":"easy"}}]}}
""".strip()


def build_direct_repair_prompt(quiz_obj: dict | None, raw_output: str, material: str, params, expected_count: int) -> str:
    profile = get_profile(params.age_group)
    quiz_json = json.dumps(quiz_obj, ensure_ascii=False, indent=2) if quiz_obj else raw_output
    examples = _style_examples(params.age_group, params.page_from, params.page_to)
    keywords = _keywords(params.age_group, params.page_from, params.page_to)

    return f"""
Ret quizzen og returnér KUN gyldig JSON.
Skriv ALT på dansk.

Målgruppe:
- Alder: {profile["label"]}
- Niveau: {profile["language_level"]}

Stil-eksempler for denne aldersgruppe og de valgte sider:
{examples}

Nøgleord fra eksemplerne i det valgte sideinterval:
{keywords}

Krav:
- Returnér præcis {expected_count} spørgsmål.
- Brug eksemplerne kun som stil- og emneguide. Kopiér dem ikke, og lav ikke rene omformuleringer.
- Brug kun oplysninger fra materialet og de valgte sider.
- Hvert spørgsmål skal have præcis 3 svarmuligheder.
- Der må kun være 1 korrekt svar.
- correct_answer skal være præcis samme tekst som options[answer_index].
- Forkerte svar må ikke være andre korrekte oplysninger fra materialet.
- Hvis et spørgsmål bygger på en liste, må de forkerte svar ikke være andre rigtige listepunkter.
- Sproget skal være naturligt dansk for målgruppen.
- Behold schemaet med alle felter.

Quiz/output der skal rettes:
{quiz_json}

Materiale:
\"\"\"
{material}
\"\"\"
""".strip()


def build_review_prompt(quiz_obj: dict, material: str, params, expected_count: int, facts_obj: dict | None = None) -> str:
    profile = get_profile(params.age_group)
    quiz_json = json.dumps(quiz_obj, ensure_ascii=False, indent=2)
    facts_json = json.dumps(facts_obj or {}, ensure_ascii=False, indent=2)
    examples = _style_examples(params.age_group, params.page_from, params.page_to)

    return f"""
Du er kvalitetskontrol for quizspørgsmål.

Skriv ALT på dansk.
Returnér KUN gyldig JSON.

Målgruppe:
- Hold: {params.age_group}
- Alder: {profile["label"]}
- Niveau: {profile["language_level"]}

Stil-eksempler for denne aldersgruppe og de valgte sider:
{examples}

Tjek alle spørgsmål for:
- Er sproget passende til målgruppen?
- Er sproget naturligt dansk uden engelske fyldord?
- Er spørgsmålet tydeligt?
- Er der kun ét korrekt svar?
- Kommer det korrekte svar fra materialet?
- Er de forkerte svar plausible men forkerte?
- Er de forkerte svar opfundne og ikke andre rigtige punkter fra en liste?
- Er spørgsmålet støttet af materialet?
- Er der variation mellem emnerne?
- Er forklaringen god og konkret?
- Er spørgsmålet for langt eller klodset formuleret?

Regler:
- Returnér mindst {expected_count} spørgsmål, hvis materialet rækker til det.
- Fjern kun spørgsmål, hvis de er ugyldige eller dubletter.
- Omskriv dårlige spørgsmål.
- Erstat dubletter med nye spørgsmål fra andre facts i materialet.
- Sørg for at hver options-liste har præcis 1 korrekt svar og 2 forkerte svar.
- Det korrekte svar skal matche correct_answer fra den valgte fact.
- Hvis flere options er rigtige ifølge materialet, skal spørgsmålet omskrives.
- Hvis en fact har list_group, må forkerte svar ikke være andre correct_answer fra samme list_group.
- Behold metadatafelterne.
- Returnér kun gyldig JSON.

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
      "correct_answer": "det korrekte svar fra materialet",
      "explanation": "kort forklaring på dansk",
      "source_page": 4,
      "source_fact_id": 1,
      "topic": "symptomer",
      "list_group": "tegn på astma",
      "difficulty": "easy"
    }}
  ]
}}

Quiz der skal gennemgås:
{quiz_json}

Facts:
{facts_json}

Materiale:
\"\"\"
{material}
\"\"\"
""".strip()


def build_single_regenerate_prompt(material: str, params, old_question: dict, existing_questions: list[dict], facts_obj: dict) -> str:
    profile = get_profile(params.age_group)
    old_q_json = json.dumps(old_question, ensure_ascii=False, indent=2)
    existing_json = json.dumps(existing_questions, ensure_ascii=False, indent=2)
    facts_json = json.dumps(facts_obj, ensure_ascii=False, indent=2)
    examples = _style_examples(params.age_group, params.page_from, params.page_to)

    return f"""
Du er quizforfatter.

Skriv ALT på dansk.
Returnér KUN gyldig JSON.

Målgruppe:
- Hold: {params.age_group}
- Alder: {profile["label"]}
- Niveau: {profile["language_level"]}

Stil-eksempler for denne aldersgruppe og de valgte sider:
{examples}

Lav PRÆCIS 1 nyt multiple choice-spørgsmål.

Vigtige regler:
- Det nye spørgsmål må ikke ligne det gamle for meget.
- Det nye spørgsmål må ikke ligne de eksisterende spørgsmål.
- Brug helst en anden fact eller et andet emne end det gamle spørgsmål.
- Brug kun facts fra listen.
- 3 svarmuligheder
- 1 korrekt svar
- Det korrekte svar skal komme fra correct_answer på den valgte fact.
- De 2 forkerte svar skal du selv opfinde.
- Forkerte svar må ikke være andre korrekte facts eller listepunkter fra materialet.
- Hvis den valgte fact har list_group, må forkerte svar ikke være andre correct_answer fra samme list_group.
- plausible men forkerte distraktorer
- naturligt dansk uden engelske fyldord; fagord fra PDF'en er tilladt
- metadatafelter skal med

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
      "correct_answer": "det korrekte svar fra materialet",
      "explanation": "kort forklaring på dansk",
      "source_page": 4,
      "source_fact_id": 1,
      "topic": "symptomer",
      "list_group": "tegn på astma",
      "difficulty": "easy"
    }}
  ]
}}

Gammelt spørgsmål:
{old_q_json}

Eksisterende spørgsmål:
{existing_json}

Facts:
{facts_json}
""".strip()
