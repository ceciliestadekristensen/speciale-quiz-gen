import re


STYLE_EXAMPLES = {
    "A": [
        {
            "source_page": 4,
            "question": "Hvad kan være et tegn på astma, når du trækker vejret?",
            "options": ["Du hoster", "Du nyser", "Du får længere hår"],
            "correct_answer": "Du hoster",
        },
        {
            "source_page": 4,
            "question": "Hvordan kan du have det, hvis du har astma?",
            "options": ["Du har ondt i tåen", "Du kan blive træt og forpustet", "Du kan have ondt i armen"],
            "correct_answer": "Du kan blive træt og forpustet",
        },
        {
            "source_page": 12,
            "question": "Hvad skal du gøre for at måle dit peak flow korrekt?",
            "options": ["Holde den på hovedet", "Synge ind i måleren", "Puste så hårdt du kan i måleren"],
            "correct_answer": "Puste så hårdt du kan i måleren",
        },
        {
            "source_page": 12,
            "question": "Hvordan skal du stå, når du måler dit peak flow?",
            "options": ["Stå op", "Ligge ned", "Sidde på gulvet"],
            "correct_answer": "Stå op",
        },
        {
            "source_page": 18,
            "question": "Hvilken farve inhalator skal du bruge, hvis du får svært ved at trække vejret?",
            "options": ["Grøn", "Blå", "Orange/Brun"],
            "correct_answer": "Blå",
        },
        {
            "source_page": 18,
            "question": "Hvilken farve har den hurtigvirkende medicin?",
            "options": ["Orange", "Blå", "Lilla"],
            "correct_answer": "Blå",
        },
        {
            "source_page": 21,
            "question": "Hvornår skal du tage din orange/brune astmamedicin?",
            "options": ["Hver dag, også når du har det godt", "Kun når du har feber", "Kun om natten"],
            "correct_answer": "Hver dag, også når du har det godt",
        },
        {
            "source_page": 21,
            "question": "Hvilken farve har den forebyggende medicin?",
            "options": ["Blå", "Rød", "Orange/Brun"],
            "correct_answer": "Orange/Brun",
        },
        {
            "source_page": 23,
            "question": "Hvilken farve har den langtidsvirkende medicin?",
            "options": ["Grøn", "Blå", "Orange"],
            "correct_answer": "Grøn",
        },
        {
            "source_page": 24,
            "question": "Hvornår må man bruge den lilla/røde medicin?",
            "options": ["Når man er mellem 2 og 5 år", "Når man er mellem 6 og 8 år", "Når man er over 12 år"],
            "correct_answer": "Når man er over 12 år",
        },
        {
            "source_page": 29,
            "question": "Hvornår skal du stoppe op og mærke efter, om du er okay?",
            "options": ["Hvis du hoster om natten", "Hvis du ikke hoster om natten", "Hvis du ikke bliver forpustet"],
            "correct_answer": "Hvis du hoster om natten",
        },
        {
            "source_page": 35,
            "question": "Hvad kan gøre din astma værre?",
            "options": ["Løb og leg", "Slik", "Vand"],
            "correct_answer": "Løb og leg",
        },
        {
            "source_page": 35,
            "question": "Hvad er noget, du kan være allergisk overfor?",
            "options": ["Sten", "Pollen", "Plastik"],
            "correct_answer": "Pollen",
        },
        {
            "source_page": 44,
            "question": "Hvilket tal skal du bruge efter tre pust i peak flowmeteret?",
            "options": ["Det laveste tal", "Det højeste tal", "Det første tal"],
            "correct_answer": "Det højeste tal",
        },
        {
            "source_page": 50,
            "question": "Hvad skal du gøre først, hvis du får et astmaanfald?",
            "options": ["Tage din medicin", "Løbe væk", "Lægge dig til at sove"],
            "correct_answer": "Tage din medicin",
        },
        {
            "source_page": 53,
            "question": "Hvordan laver man vejrtrækningsøvelsen?",
            "options": [
                "Hoppe op og ned imens man puster ud",
                "Trække vejret ind gennem næsen og holde vejret",
                "Trække vejret ind gennem næsen og puste ud gennem munden"],
            "correct_answer": "Trække vejret ind gennem næsen og puste ud gennem munden",
        },
        {
            "source_page": 57,
            "question": "Må du lege og være aktiv, når du har astma?",
            "options": ["Ja", "Nej", "Kun om natten"],
            "correct_answer": "Ja",
        },
    ],
    "B": [
        {
            "source_page": 4,
            "question": "Hvad er et typisk symptom på astma?",
            "options": ["Pibende eller hvæsende vejrtrækning", "Ondt i hovedet", "Kløe på halsen"],
            "correct_answer": "Pibende eller hvæsende vejrtrækning",
        },
        {
            "source_page": 4,
            "question": "Hvornår kan symptomer på astma ofte blive værre?",
            "options": ["Kun midt på dagen", "Om natten eller om morgenen", "Kun når man spiser"],
            "correct_answer": "Om natten eller om morgenen",
        },
        {
            "source_page": 12,
            "question": "Hvor mange gange skal du puste i peak flowmeteret for at få et korrekt resultat?",
            "options": ["8 gange", "1 gang", "3 gange"],
            "correct_answer": "3 gange",
        },
        {
            "source_page": 12,
            "question": "Hvilket tal skal du bruge efter tre pust i peak flowmeteret?",
            "options": ["Det laveste tal", "Det højeste tal", "Det første tal"],
            "correct_answer": "Det højeste tal",
        },
        {
            "source_page": 18,
            "question": "Hvad bruges den blå inhalator som?",
            "options": ["Forebyggende medicin", "Hurtigvirkende medicin", "Langtidsvirkende medicin"],
            "correct_answer": "Hurtigvirkende medicin",
        },
        {
            "source_page": 18,
            "question": "Hvor hurtigt virker den blå astmamedicin?",
            "options": ["Efter få minutter", "Efter flere dage", "Efter en uge"],
            "correct_answer": "Efter få minutter",
        },
        {
            "source_page": 21,
            "question": "Hvad er formålet med forebyggende astmamedicin?",
            "options": ["At give energi", "At virke med det samme", "At beskytte luftvejene over tid"],
            "correct_answer": "At beskytte luftvejene over tid",
        },
        {
            "source_page": 21,
            "question": "Hvilken farve har den forebyggende medicin?",
            "options": ["Blå", "Orange/Brun", "Grøn"],
            "correct_answer": "Orange/Brun",
        },
        {
            "source_page": 23,
            "question": "Hvor længe virker langtidsvirkende medicin cirka?",
            "options": ["12 timer", "5 minutter", "1 minut"],
            "correct_answer": "12 timer",
        },
        {
            "source_page": 23,
            "question": "Hvordan bruges den grønne medicin?",
            "options": [ "Maks en gang om dagen i nødstilfælde", "Lige inden man dyrker motion", "Som langtidsvirkende"],
            "correct_answer": "Som langtidsvirkende",
        },
        {
            "source_page": 24,
            "question": "Hvad indeholder kombinationsmedicin?",
            "options": ["Både forebyggende og anfaldsmedicin", "Kun smertestillende", "Kun vitaminer"],
            "correct_answer": "Både forebyggende og anfaldsmedicin",
        },
        {
            "source_page": 29,
            "question": "Hvornår er din astma blandt andet velkontrolleret?",
            "options": [ "Hvis du hoster om natten", "Hvis du ikke hoster om natten", "Hvis dit peak flow falder mere end 20 %"],
            "correct_answer": "Hvis du ikke hoster om natten",
        },
        {
            "source_page": 35,
            "question": "Hvad kan ikke gøre din astma værre?",
            "options": ["Støv", "At gå i skole", "Sport"],
            "correct_answer": "At gå i skole",
        },
        {
            "source_page": 35,
            "question": "Hvilket er ikke en irritant når man har astma?",
            "options": ["Røg", "At lære om sin astma", "Dårligt inde klima"],
            "correct_answer": "At lære om sin astma",
        },
        {
            "source_page": 37,
            "question": "Hvad er allergi?",
            "options": ["Når man bliver træt", "Når kroppen reagerer på noget, den ikke kan tåle", "Når man fryser"],
            "correct_answer": "Når kroppen reagerer på noget, den ikke kan tåle",
        },
        {
            "source_page": 44,
            "question": "Hvilket tal skal du bruge efter tre pust i peak flowmeteret?",
            "options": ["Det laveste tal", "Det højeste tal", "Det første tal"],
            "correct_answer": "Det højeste tal",
        },
        {
            "source_page": 50,
            "question": "Hvad skal du gøre, hvis din medicin ikke virker ved et astmaanfald?",
            "options": ["Få en voksen til at hjælpe og ringe efter hjælp", "Ignorere det", "Gå i seng"],
            "correct_answer": "Få en voksen til at hjælpe og ringe efter hjælp",
        },
        {
            "source_page": 53,
            "question": "Hvordan laver man vejrtrækningsøvelsen?",
            "options": [
                "Hoppe op og ned imens man puster ud",
                "Trække vejret ind gennem næsen og holde vejret",
                "Trække vejret ind gennem næsen og puste ud gennem munden"],
            "correct_answer": "Trække vejret ind gennem næsen og puste ud gennem munden",
        },
        {
            "source_page": 57,
            "question": "Hvor meget skal man være fysisk aktiv hver dag?",
            "options": ["Ca. 1 time", "5 minutter", "Slet ikke"],
            "correct_answer": "Ca. 1 time",
        },
    ],
    "C": [
        {
            "source_page": 4,
            "question": "Hvilket symptom kan skyldes astma?",
            "options": ["Ofte forkølet", "Pludselig hoste", "Rødme og udslæt"],
            "correct_answer": "Pludselig hoste",
        },
        {
            "source_page": 4,
            "question": "Hvilket symptom er typisk ved astma under fysisk aktivitet?",
            "options": ["Man bliver hurtigt forpustet", "Man får værre syn", "Man får ondt i maven"],
            "correct_answer": "Man bliver hurtigt forpustet",
        },
        {
            "source_page": 12,
            "question": "Hvad må du ikke gøre med tungen under en peak flowmåling?",
            "options": ["Trække den tilbage", "Sætte den foran mundstykket", "Holde den stille"],
            "correct_answer": "Sætte den foran mundstykket",
        },
        {
            "source_page": 12,
            "question": "Hvorfor laver man flere målinger i peak flowmeteret?",
            "options": ["For at finde det mest præcise resultat", "For at træne lungerne", "For at se hvor hårdt du kan puste"],
            "correct_answer": "For at finde det mest præcise resultat",
        },
        {
            "source_page": 18,
            "question": "Hvor mange gange om dagen må man tage den hurtigvirkende anfaldsmedicin?",
            "options": ["2 gange om dagen", "8 gange om dagen", "12 gange om dagen"],
            "correct_answer": "8 gange om dagen",
        },
        {
            "source_page": 18,
            "question": "Hvor længe virker hurtigtvirkende medicin cirka?",
            "options": ["1 uge", "24 timer", "1 ugeOmkring 3 timer"],
            "correct_answer": "Omkring 3 timer",
        },
        {
            "source_page": 21,
            "question": "Hvornår skal man bruge den orange/brune medicin?",
            "options": [
                "Som anfaldsmedicin med hurtig virkning",
                "Op til 8 gange om dagen ved motion",
                "Som forebyggende medicin fast hver dag"],
            "correct_answer": "Som forebyggende medicin fast hver dag",
        },
        {
            "source_page": 21,
            "question": "Hvornår opnår forebyggende medicin fuld effekt ved astma?",
            "options": ["Med det samme", "Efter nogle uger", "Efter 1 time"],
            "correct_answer": "Efter nogle uger",
        },
        {
            "source_page": 23,
            "question": "Hvor ofte skal du tage den langtidsvirkende astmamedicin?",
            "options": ["2 gange om ugen", "Hver anden dag", "Fast hver dag"],
            "correct_answer": "Fast hver dag",
        },
        {
            "source_page": 23,
            "question": "Hvordan bruges den grønne medicin?",
            "options": ["Maks en gang om dagen i nødstilfælde", "Lige inden man dyrker motion", "Som langtidsvirkende forebyggelse"],
            "correct_answer": "Som langtidsvirkende forebyggelse",
        },
        {
            "source_page": 24,
            "question": "Hvad er kombinationsmedicinen en blanding af?",
            "options": ["Den brune/orange og den grønne medicin", "Den blå medicin og den grønne medicin", "Den brune/orange og den blå medicin "],
            "correct_answer": "Den brune/orange og den grønne medicin",
        },
        {
            "source_page": 29,
            "question": "Hvornår er din astma blandt andet delvist kontrolleret?",
            "options": ["Hvis dit peak flow falder 15-20 %", "Hvis dit peak flow falder mere end 30 %", "Hvis dit peak flow falder mere end 20 %"],
            "correct_answer": "Hvis dit peak flow falder 15-20 %",
        },
        {
            "source_page": 35,
            "question": "Hvad er allergi?",
            "options": ["Når man bliver træt", "Når kroppen reagerer på noget, den ikke kan tåle", "Når man fryser"],
            "correct_answer": "Når kroppen reagerer på noget, den ikke kan tåle",
        },
        {
            "source_page": 44,
            "question": "Hvilket tal skal du bruge efter tre pust i peak flowmeteret?",
            "options": ["Det laveste tal", "Det højeste tal", "Det første tal"],
            "correct_answer": "Det højeste tal",
        },
        {
            "source_page": 50,
            "question": "Hvor ofte kan man tage medicin ved et alvorligt astmaanfald ifølge planen?",
            "options": ["Kun én gang om dagen", "Kun én gang", "Hvert 5. minut i flere omgange"],
            "correct_answer": "Hvert 5. minut i flere omgange",
        },
        {
            "source_page": 53,
            "question": "Hvordan laver man vejrtrækningsøvelsen?",
            "options": ["Træk vejret ind gennem næsen og pust ud gennem munden", "Træk vejret ind gennem næsen og hold vejret", "Træk vejret ind gennem munden og pust ud gennem munden"],
            "correct_answer": "Træk vejret ind gennem næsen og pust ud gennem munden",
        },
        {
            "source_page": 57,
            "question": "Hvad skal du huske før fysisk aktivitet?",
            "options": ["At lade din astmamedicin blive hjemme", "At tage din forebyggende medicin med", "At have din hurtigvirkende medicin med"],
            "correct_answer": "At have din hurtigvirkende medicin med",
        },
    ],
    "UNG": [
        {
            "source_page": 5,
            "question": "Hvad sker der i lungerne ved astma?",
            "options": ["Slimhinderne hæver og danner ekstra slim, og musklerne trækker sig sammen", "Lungerne bliver større og optager mere ilt", "Blodet stopper med at cirkulere i lungerne"],
            "correct_answer": "Slimhinderne hæver og danner ekstra slim, og musklerne trækker sig sammen",
        },
        {
            "source_page": 5,
            "question": "Hvorfor bliver det sværere at trække vejret ved astma?",
            "options": ["Hjertet slår langsommere", "Der bliver mindre plads i luftrørene", "Kroppen mangler CO2"],
            "correct_answer": "Der bliver mindre plads i luftrørene",
        },
        {
            "source_page": 7,
            "question": "Hvilket symptom er typisk ved astma?",
            "options": ["Pibende eller hvæsende vejrtrækning", "Ondt i hovedet", "Synsforstyrrelser"],
            "correct_answer": "Pibende eller hvæsende vejrtrækning",
        },
        {
            "source_page": 7,
            "question": "Hvornår oplever mange symptomer på astma?",
            "options": ["Kun efter måltider", "Kun om eftermiddagen", "Under motion"],
            "correct_answer": "Under motion",
        },
        {
            "source_page": 9,
            "question": "Hvordan udfører du korrekt en peak flowmåling?",
            "options": ["Puster så kraftigt som muligt efter en dyb indånding", "Trækker vejret langsomt ind og puster ud", "Holder vejret i 10 sekunder og puster"],
            "correct_answer": "Puster så kraftigt som muligt efter en dyb indånding",
        },
        {
            "source_page": 9,
            "question": "Hvilket resultat bruger du ved peak flowmåling?",
            "options": ["Gennemsnittet af alle målinger", "Det højeste af tre målinger", "Den første måling"],
            "correct_answer": "Det højeste af tre målinger",
        },
        {
            "source_page": 12,
            "question": "Hvad kendetegner den velkontrollerede astmazone?",
            "options": ["Behov for medicin hele tiden", "Hyppig hoste", "Ingen symptomer og ingen begrænsning af aktiviteter"],
            "correct_answer": "Ingen symptomer og ingen begrænsning af aktiviteter",
        },
        {
            "source_page": 14,
            "question": "Hvad er effekten af den blå astmamedicin?",
            "options": ["Hurtigtvirkende medicin", "Forebyggende medicin", "Langtidsvirkende medicin"],
            "correct_answer": "Hurtigtvirkende medicin",
        },
        {
            "source_page": 14,
            "question": "Hvor hurtigt virker hurtigtvirkende medicin typisk?",
            "options": ["Efter 2 dage", "Efter 2 timer", "Efter 1-3 minutter"],
            "correct_answer": "Efter 1-3 minutter",
        },
        {
            "source_page": 15,
            "question": "Hvad er formålet med forebyggende medicin ved astma?",
            "options": ["At reducere hævelse i slimhinderne og beskytte luftvejene", "At give hurtig lindring ved anfald", "At øge pulsen"],
            "correct_answer": "At reducere hævelse i slimhinderne og beskytte luftvejene",
        },
        {
            "source_page": 15,
            "question": "Hvornår skal forebyggende medicin tages?",
            "options": ["Kun ved symptomer", "Hver dag, også når man har det godt", "Kun før sport"],
            "correct_answer": "Hver dag, også når man har det godt",
        },
        {
            "source_page": 16,
            "question": "Hvad er kendetegnende for langtidsvirkende astmamedicin?",
            "options": ["Den virker kun en gang", "Den virker kun i få minutter", "Den virker i op til ca. 12 timer"],
            "correct_answer": "Den virker i op til ca. 12 timer",
        },
        {
            "source_page": 17,
            "question": "Hvilke to mediciner består kombinationsmedicinen af?",
            "options": ["Den blå og den grønne medicin", "Den orange/brune og den grønne medicin", "Den blå og den orange/brune medicin"],
            "correct_answer": "Den orange/brune og den grønne medicin",
        },
        {
            "source_page": 17,
            "question": "Hvor mange timers virkning har kombinationsmedicinen?",
            "options": ["12 timer", "48 timer", "24 timer"],
            "correct_answer": "24 timer",
        },
        {
            "source_page": 19,
            "question": "Hvad bruges Montelukast altid sammen med?",
            "options": ["Hurtigvirkende anfaldsmedicin", "Forebyggende inhalationsmedicin", "Kombinationsmedicin"],
            "correct_answer": "Forebyggende inhalationsmedicin",
        },
        {
            "source_page": 19,
            "question": "Hvordan skal Montelukast typisk tages?",
            "options": ["Som tablet en gang dagligt", "Som inhalation flere gange i timen", "Kun ved anfald"],
            "correct_answer": "Som tablet en gang dagligt",
        },
        {
            "source_page": 20,
            "question": "Hvilket er ikke en irritant når man har astma?",
            "options": ["Røg", "At lære om sin astma", "Dårligt inde klima"],
            "correct_answer": "At lære om sin astma",
        },
        {
            "source_page": 20,
            "question": "Hvad sker der ved allergi i kroppen?",
            "options": ["Immunsystemet overreagerer på noget, man ikke kan tåle", "Kroppen stopper med at reagere", "Blodet bliver tykkere"],
            "correct_answer": "Immunsystemet overreagerer på noget, man ikke kan tåle",
        },
        {
            "source_page": 28,
            "question": "Hvad kan du selv gøre for at kontrollere din astma i hverdagen?",
            "options": ["Tage medicin fast og være opmærksom på symptomer", "Undgå al aktivitet", "Stoppe med medicin"],
            "correct_answer": "Tage medicin fast og være opmærksom på symptomer",
        },
        {
            "source_page": 31,
            "question": "Hvad er vigtigt at huske før fysisk aktivitet med astma?",
            "options": ["Undgå at trække vejret dybt", "Tage hurtigtvirkende medicin med og varme op", "Tage den langtidsvirkende medicin"],
            "correct_answer": "Tage hurtigtvirkende medicin med og varme op",
        },
        {
            "source_page": 32,
            "question": "Hvad er en fordel ved fysisk aktivitet, når man har astma?",
            "options": ["Man bliver mindre forpustet med bedre kondition", "Astma forsvinder med det samme", "Man får dårligere vejrtrækning"],
            "correct_answer": "Man bliver mindre forpustet med bedre kondition",
        },
        {
            "source_page": 33,
            "question": "Hvad er det første, man skal gøre ved et akut astmaanfald?",
            "options": ["Tage 2 sug af den forebyggende medicin", "Lægge sig ned og vente", "Tage 2 sug af hurtigtvirkende medicin"],
            "correct_answer": "Tage 2 sug af hurtigtvirkende medicin",
        },
        {
            "source_page": 33,
            "question": "Hvornår skal man søge akut hjælp ved astmaanfald?",
            "options": ["Hvis medicinen ikke virker efter gentagne doser", "Med det samme altid", "Dagen efter"],
            "correct_answer": "Hvis medicinen ikke virker efter gentagne doser",
        },
    ],
}


def get_style_examples(age_group: str) -> list[dict]:
    return STYLE_EXAMPLES.get(age_group.upper(), STYLE_EXAMPLES["B"])


def get_examples_for_page_range(
    age_group: str,
    page_from: int | None = None,
    page_to: int | None = None,
) -> list[dict]:
    examples = get_style_examples(age_group)
    start = 1 if page_from is None else page_from
    end = 10**9 if page_to is None else page_to

    return [
        example
        for example in examples
        if start <= int(example.get("source_page", 0)) <= end
    ]


def get_keywords_for_page_range(
    age_group: str,
    page_from: int | None = None,
    page_to: int | None = None,
    max_keywords: int = 30,
) -> list[str]:
    examples = get_examples_for_page_range(age_group, page_from, page_to)
    stop_words = {
        "hvad", "hvor", "hvilken", "hvordan", "hvornår", "hvorfor", "skal", "kan",
        "man", "du", "din", "dit", "dine", "der", "det", "den", "deres", "hvis",
        "når", "med", "som", "for", "til", "fra", "eller", "og", "på", "i", "at",
        "er", "en", "et", "har", "have", "bruge", "bruges", "gøre", "godt", "kun",
        "ikke", "mere", "mindre", "altid", "om", "sig", "selv", "bliver", "får",
        "være", "blive", "korrekt", "typisk", "noget",
    }
    seen: set[str] = set()
    keywords: list[str] = []

    for example in examples:
        parts = [
            str(example.get("question", "")),
            str(example.get("correct_answer", "")),
        ]
        text = " ".join(parts).lower()
        for raw in re.findall(r"[a-zæøå0-9/]+", text):
            word = raw.strip("/")
            if len(word) < 3 or word in stop_words:
                continue
            if word not in seen:
                seen.add(word)
                keywords.append(word)
            if len(keywords) >= max_keywords:
                return keywords

    return keywords
