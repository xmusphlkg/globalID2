"""Explicitly reviewed Iceland series identities and source-label normalization."""
from __future__ import annotations

import re
import unicodedata
from .contracts import ReviewedSeries, ReviewedLegacySeries


HISTORY_SOURCE_ID = "SRC_IS_DOH_HISTORY"


LEGACY_SOURCE_ID = "SRC_IS_DOH_LEGACY_ICD"


HISTORY_SOURCE_NAME = "Iceland Directorate of Health Historical Registry"


LEGACY_SOURCE_NAME = "Iceland Directorate of Health Legacy ICD Monthly"


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    normalized = unicodedata.normalize("NFKC", str(value).replace("\ufeff", ""))
    return " ".join(normalized.split()).strip()


def _label_key(value: object) -> str:
    value_text = _text(value)
    value_text = re.sub(r"\s*\*+\s*$", "", value_text)
    value_text = value_text.replace("–", "-").replace("—", "-")
    return value_text.casefold().strip(" .:;")


def _explanation_key(value: object) -> str:
    key = _label_key(value)
    key = re.sub(r"\s*\([^)]*\)\s*$", "", key)
    return key.strip()


def _normalize_icd(value: object) -> str:
    code = _text(value).upper().replace("–", "-").replace("—", "-")
    code = re.sub(r"\s*,\s*", ", ", code)
    code = re.sub(r"\s*-\s*", "-", code)
    return " ".join(code.split())


def _as_year(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and float(value).is_integer():
        year = int(value)
        return year if 1900 <= year <= 2100 else None
    text_value = _text(value)
    if re.fullmatch(r"(?:19|20)\d{2}", text_value):
        return int(text_value)
    return None


MONTHS = {
    "jan": 1,
    "january": 1,
    "janúar": 1,
    "feb": 2,
    "february": 2,
    "febrúar": 2,
    "mar": 3,
    "march": 3,
    "mars": 3,
    "apr": 4,
    "april": 4,
    "apríl": 4,
    "may": 5,
    "maí": 5,
    "jun": 6,
    "jún": 6,
    "june": 6,
    "júní": 6,
    "jul": 7,
    "júl": 7,
    "july": 7,
    "júlí": 7,
    "aug": 8,
    "ág": 8,
    "ágú": 8,
    "august": 8,
    "ágúst": 8,
    "sep": 9,
    "september": 9,
    "oct": 10,
    "okt": 10,
    "october": 10,
    "október": 10,
    "nov": 11,
    "nóv": 11,
    "november": 11,
    "nóvember": 11,
    "dec": 12,
    "des": 12,
    "december": 12,
    "desember": 12,
}


def _month_number(value: object) -> int | None:
    token = _label_key(value).split(" ", 1)[0].strip(".:")
    return MONTHS.get(token)


ANNUAL_SERIES: tuple[ReviewedSeries, ...] = (
    ReviewedSeries("aids", "AIDS", "D005", "Alnæmi", "AIDS"),
    ReviewedSeries("cysticercosis", "CYSTICERCOSIS", "D088", "Bandormslirfusýki", "Cysticercosis"),
    ReviewedSeries("diphtheria", "DIPHTHERIA", "D029", "Barnaveiki", "Diphtheria"),
    ReviewedSeries("dengue", "DENGUE", "D021", "Beinbrunasótt", "Dengue"),
    ReviewedSeries("tuberculosis", "TUBERCULOSIS", "D025", "Berklar", "Tuberculosis"),
    ReviewedSeries("viral_hemorrhagic_fever", "VIRAL_HEMORRHAGIC_FEVER", "D127", "Blæðandi veiruhitasóttir", "Viral hemorrhagic fevers", ("Hemorrhagic viral fever",)),
    ReviewedSeries("smallpox", "SMALLPOX", "D064", "Bólusótt", "Smallpox"),
    ReviewedSeries("botulism", "BOTULISM", "D091", "Bótúlismi", "Botulism"),
    ReviewedSeries("esbl_ampc", "ESBL_AMPC", "D237", "Breiðvirkir betalaktamasamyndandi sýklar (ESBL/AmpC)", "ESBL/AmpC-producing organism surveillance", ("Breiðvirkir betalaktamasamyndandi sýklar (ESBL)", "Extended Spectrum Beta Lactamase (ESBL)", "Extended Spectrum Beta Lactamase (ESBL)*")),
    ReviewedSeries("covid_19", "COVID_19", "D004", "COVID-19", "COVID-19"),
    ReviewedSeries("sars", "SARS", "D003", "Córónaveirulungnabólga", "SARS"),
    ReviewedSeries("cjd", "CJD", "D144", "Creutzfeldt Jakobs veiki/afbrigði", "Creutzfeldt-Jakob disease", ("New variant Creutzfeldt Jakobs Disease (CJD)",)),
    ReviewedSeries("stec", "STEC", "D115", "Enterohaemorrhagisk E. coli sýking", "Shiga toxin-producing Escherichia coli infection", ("Enterohemorrhagisk E. coli sýking", "Enterohaemorrhagic E. coli infection")),
    ReviewedSeries("giardiasis", "GIARDIASIS", "D099", "Giardiasis", "Giardiasis"),
    ReviewedSeries("yellow_fever", "YELLOW_FEVER", "D059", "Gulusótt", "Yellow fever"),
    ReviewedSeries("hib", "HIB", "D100", "Haemofilus influenzae sýking b", "Haemophilus influenzae type b", ("Hemofilus influenzae sýking b", "Haemophilus influenzae infection type b", "Hemophilus influenzae infection type b")),
    ReviewedSeries("mumps", "MUMPS", "D039", "Hettusótt", "Mumps"),
    ReviewedSeries("tularemia", "TULAREMIA", "D123", "Hérasótt", "Tularemia"),
    ReviewedSeries("hiv", "HIV", "D162", "HIV sýking (human immunod. virus)", "HIV infection"),
    ReviewedSeries("leprosy", "LEPROSY", "D042", "Holdsveiki", "Leprosy", ("Lepra",)),
    ReviewedSeries("q_fever", "Q_FEVER", "D113", "Huldusótt", "Q fever", ("Q-fever",)),
    ReviewedSeries("rabies", "RABIES", "D019", "Hundaæði", "Rabies"),
    ReviewedSeries("pandemic_influenza_h1n1", "PANDEMIC_INFLUENZA_A_H1N1_2009", "D038", "Inflúensa A (H1N1) 2009", "Influenza A(H1N1)pdm09", ("Pandemic influenzae A(H1N1) 2009",)),
    ReviewedSeries("influenza_a_h3", "INFLUENZA_A_H3", "D038", "Inflúensa A H3", "Influenza A(H3)", ("Influenzae A(H3)",)),
    ReviewedSeries("invasive_h_influenzae", "INVASIVE_HAEMOPHILUS_INFLUENZAE", "D100", "Ífarandi Haemophilus influenzae sýking", "Invasive Haemophilus influenzae", ("Ífarandi Hemófílus inflúensusýking", "Invasive Hemophilus influenzae")),
    ReviewedSeries("invasive_pneumococcal", "INVASIVE_PNEUMOCOCCAL", "D106", "Ífarandi pneumókokkasýkingar", "Invasive pneumococcal disease", ("Invasive pneumococcal infections",)),
    ReviewedSeries("yersiniosis", "YERSINIOSIS", "D155", "Jersiníusýking", "Yersiniosis", ("Yersinia enterocolitica, Yersinia pseudotuberculosis",)),
    ReviewedSeries("campylobacteriosis", "CAMPYLOBACTERIOSIS", "D092", "Kampýlóbaktersýking", "Campylobacteriosis"),
    ReviewedSeries("pertussis", "PERTUSSIS", "D028", "Kikhósti", "Pertussis"),
    ReviewedSeries("chlamydia", "CHLAMYDIA", "D094", "Klamydíusýking", "Chlamydia trachomatis infection", ("Chlamydia trachomatis",)),
    ReviewedSeries("cholera", "CHOLERA", "D002", "Kólera og kólerulíkar sýkingar", "Cholera"),
    ReviewedSeries("cryptosporidiosis", "CRYPTOSPORIDIOSIS", "D096", "Launsporasýking (cryptósporidium sýking)", "Cryptosporidiosis"),
    ReviewedSeries("legionellosis", "LEGIONELLOSIS", "D107", "Legíónellusýking", "Legionellosis"),
    ReviewedSeries("gonorrhea", "GONORRHEA", "D033", "Lekandi", "Gonorrhea", ("Gonorrhoea",)),
    ReviewedSeries("leptospirosis", "LEPTOSPIROSIS", "D035", "Leptóspirusýking", "Leptospirosis"),
    ReviewedSeries("hepatitis_a", "HEPATITIS_A", "D007", "Lifrarbólga A", "Hepatitis A"),
    ReviewedSeries("hepatitis_b_combined", "HEPATITIS_B_COMBINED", "D008", "Lifrarbólga B (bráð, viðvarandi)", "Hepatitis B (acute and chronic combined)", ("Hepatitis B (acute, chronic)",)),
    ReviewedSeries("hepatitis_c", "HEPATITIS_C", "D009", "Lifrarbólga C", "Hepatitis C"),
    ReviewedSeries("hepatitis_e", "HEPATITIS_E", "D011", "Lifrarbólga E", "Hepatitis E"),
    ReviewedSeries("listeriosis", "LISTERIOSIS", "D108", "Listeríusýking", "Listeriosis"),
    ReviewedSeries("poliomyelitis", "POLIOMYELITIS", "D013", "Lömunarveiki", "Poliomyelitis"),
    ReviewedSeries("malaria", "MALARIA", "D037", "Malaría", "Malaria"),
    ReviewedSeries("meningococcal", "MENINGOCOCCAL", "D110", "Meningókokkasjúkdómur", "Meningococcal disease"),
    ReviewedSeries("mrsa", "MRSA", "D236", "Methicillin ónæmur stafýlokokkus aureus, MÓSA", "MRSA surveillance", ("Methicillin resistant Staphylococcus aureus (MRSA)",)),
    ReviewedSeries("anthrax", "ANTHRAX", "D023", "Miltisbrandur", "Anthrax"),
    ReviewedSeries("measles", "MEASLES", "D017", "Mislingar", "Measles"),
    ReviewedSeries("rubella", "RUBELLA", "D040", "Rauðir hundar", "Rubella"),
    ReviewedSeries("salmonellosis", "SALMONELLOSIS", "D114", "Salmonellusýking", "Salmonellosis"),
    ReviewedSeries("syphilis", "SYPHILIS", "D034", "Sárasótt", "Syphilis"),
    ReviewedSeries("shigellosis", "SHIGELLOSIS", "D105", "Sígellusýking", "Shigellosis"),
    ReviewedSeries("tetanus", "TETANUS", "D120", "Stífkrampi", "Tetanus"),
    ReviewedSeries("echinococcosis", "ECHINOCOCCOSIS", "D045", "Sullaveiki", "Echinococcosis"),
    ReviewedSeries("plague", "PLAGUE", "D001", "Svarti dauði", "Plague"),
    ReviewedSeries("toxoplasmosis", "TOXOPLASMOSIS", "D085", "Toxóplasmasýking", "Toxoplasmosis"),
    ReviewedSeries("typhoid_paratyphoid", "TYPHOID_PARATYPHOID", "D026", "Taugaveiki/taugaveikibróðir", "Typhoid and paratyphoid fever", ("Typhoid/paratyphoid fever",)),
    ReviewedSeries("trichinellosis", "TRICHINELLOSIS", "D122", "Tríkínusýking", "Trichinellosis", ("Trichinosis/Trichinellosis",)),
    ReviewedSeries("vre", "VRE", "D125", "Vankomýcín ónæmur enterókokkur", "Vancomycin-resistant Enterococcus (VRE)", ("Vancomycin resistant Enterococcus (VRE)",)),
    ReviewedSeries("west_nile", "WEST_NILE", "D128", "Vesturnílarveirusótt", "West Nile virus infection", ("West Nile Virus Infection",)),
    ReviewedSeries("brucellosis", "BRUCELLOSIS", "D032", "Öldusótt", "Brucellosis"),
)


MONTHLY_SERIES: dict[str, ReviewedSeries] = {
    item.key: item
    for item in (
        ReviewedSeries("esbl_ampc", "ESBL_AMPC", "D237", "Breiðvirkir beta-laktamasar (ESBL)", "ESBL/AmpC-producing organism surveillance"),
        ReviewedSeries("giardiasis", "GIARDIASIS", "D099", "Giardíusýking", "Giardiasis"),
        ReviewedSeries("hiv", "HIV", "D162", "HIV sýking", "HIV infection"),
        ReviewedSeries("campylobacteriosis", "CAMPYLOBACTERIOSIS", "D092", "Kampýlóbaktersýking", "Campylobacteriosis"),
        ReviewedSeries("pertussis", "PERTUSSIS", "D028", "Kikhósti", "Pertussis"),
        ReviewedSeries("chlamydia", "CHLAMYDIA", "D094", "Klamydíusýking", "Chlamydia trachomatis infection"),
        ReviewedSeries("gonorrhea", "GONORRHEA", "D033", "Lekandi", "Gonorrhea"),
        ReviewedSeries("hepatitis_a", "HEPATITIS_A", "D007", "Lifrarbólga A", "Hepatitis A"),
        ReviewedSeries("hepatitis_b_combined", "HEPATITIS_B_COMBINED", "D008", "Lifrarbólga B", "Hepatitis B (acute and chronic combined)"),
        ReviewedSeries("hepatitis_c", "HEPATITIS_C", "D009", "Lifrarbólga C", "Hepatitis C"),
        ReviewedSeries("invasive_pneumococcal", "INVASIVE_PNEUMOCOCCAL", "D106", "Ífarandi pneumókokkasýking", "Invasive pneumococcal disease"),
        ReviewedSeries("salmonellosis", "SALMONELLOSIS", "D114", "Salmonellusýking", "Salmonellosis"),
        ReviewedSeries("syphilis", "SYPHILIS", "D034", "Sárasótt", "Syphilis"),
        ReviewedSeries("vre", "VRE", "D125", "Vankómýsínónæmir enterókokkar (VRE)", "Vancomycin-resistant Enterococcus (VRE)"),
    )
}


LEGACY_SERIES: tuple[ReviewedLegacySeries, ...] = (
    ReviewedLegacySeries("B01_HLAUPABOLA", "D054", "B01", "Hlaupabóla", "Varicella"),
    ReviewedLegacySeries("B01_B01_9_HLAUPABOLA", "D054", "B01-B01.9", "Hlaupabóla", "Varicella"),
    ReviewedLegacySeries("J10_J11_INFLUENSA", "D038", "J10-J11", "Inflúensa", "Influenza"),
    ReviewedLegacySeries("J09_J10_J10_8_U05_9_STADFEST_INFLUENSA", "D038", "J09, J10-J10.8, U05.9", "Staðfest inflúensa", "Laboratory-confirmed influenza"),
    ReviewedLegacySeries("B02_RISTILL", "D063", "B02", "Ristill", "Zoster"),
    ReviewedLegacySeries("B02_B02_9_RISTILL", "D063", "B02-B02.9", "Ristill", "Zoster"),
    ReviewedLegacySeries("A38_SKARLATSOTT", "D031", "A38", "Skarlatsótt", "Scarlet fever"),
    ReviewedLegacySeries("A38_SKARLATSSOTT", "D031", "A38", "Skarlatssótt", "Scarlet fever"),
    ReviewedLegacySeries("J02_0_J03_0_STREP_PHARYNGITIS_LIST", "D224", "J02.0, J03.0", "Streptókokka-hálsbólga", "Group A streptococcal pharyngitis"),
    ReviewedLegacySeries("J02_0_J03_0_STREP_PHARYNGITIS_RANGE", "D224", "J02.0-J03.0", "Streptókokka-hálsbólga", "Group A streptococcal pharyngitis"),
    ReviewedLegacySeries("A09_BRADUR_NIDURGANGUR", "D047", "A09", "Bráður niðurgangur", "Acute infectious diarrhea"),
    ReviewedLegacySeries("A09_NIDURGANGUR", "D047", "A09", "Niðurgangur", "Infectious diarrhea"),
    ReviewedLegacySeries("A37_KIKHOSTI", "D028", "A37", "Kikhósti", "Pertussis"),
    ReviewedLegacySeries("A36_BARNAVEIKI", "D029", "A36", "Barnaveiki", "Diphtheria"),
    ReviewedLegacySeries("B26_HETTUSOTT", "D039", "B26", "Hettusótt", "Mumps"),
    ReviewedLegacySeries("B05_MISLINGAR", "D017", "B05", "Mislingar", "Measles"),
    ReviewedLegacySeries("B06_RAUDIR_HUNDAR", "D040", "B06", "Rauðir hundar", "Rubella"),
    ReviewedLegacySeries("A35_STIFKRAMPI", "D120", "A35", "Stífkrampi", "Tetanus"),
    ReviewedLegacySeries("B58_TOXOPLASMASYKING", "D085", "B58", "Toxóplasmasýking", "Toxoplasmosis"),
    ReviewedLegacySeries("B54_MYRARKALDA", "D037", "B54", "Mýrarkalda", "Malaria"),
    ReviewedLegacySeries("B54_MYRARKALDA_MALARIA", "D037", "B54", "Mýrarkalda (malaría)", "Malaria"),
    ReviewedLegacySeries("A70_PSITTACOSIS", "D112", "A70", "Psittacosis", "Psittacosis"),
    ReviewedLegacySeries("A70_FYLASOTT", "D112", "A70", "Fýlasótt (psittacosis)", "Psittacosis"),
    ReviewedLegacySeries("A69_2_LYME", "D109", "A69.2", "Lyme sjúkdómur", "Lyme disease"),
    ReviewedLegacySeries("A69_2_LYME_BORRELIOSIS", "D109", "A69.2", "Lyme sjúkdómur (borreliosis)", "Lyme disease"),
    ReviewedLegacySeries("G00_BACTERIAL_MENINGITIS", "D135", "G00", "Heilahimnubólga af völdum sýkla", "Bacterial meningitis"),
    ReviewedLegacySeries("G00_RANGE_BACTERIAL_MENINGITIS", "D135", "G00, G00.2, G00.3-G00.9", "Heilahimnubólga af völdum sýkla", "Bacterial meningitis"),
    ReviewedLegacySeries("G00_RANGE_BACTERIAL_MENINGITIDES", "D135", "G00, G00.2, G00.3-G00.9", "Heilahimnubólgur af völdum baktería", "Bacterial meningitis"),
    ReviewedLegacySeries("A08_0_ROTAVIRUS", "D199", "A08.0", "Rótaveirusýking", "Rotavirus infection"),
    ReviewedLegacySeries("J12_1_J20_5_J21_0_RSV", "D142", "J12.1, J20.5, J21.0", "RS veirusýking", "Respiratory syncytial virus infection"),
)


def _build_annual_aliases() -> dict[str, ReviewedSeries]:
    lookup: dict[str, ReviewedSeries] = {}
    for definition in ANNUAL_SERIES:
        for alias in (definition.label_is, definition.label_en, *definition.aliases):
            key = _label_key(alias)
            prior = lookup.get(key)
            if prior is not None and prior != definition:
                raise RuntimeError(f"Conflicting annual Iceland alias: {alias!r}")
            lookup[key] = definition
    return lookup


ANNUAL_ALIAS_LOOKUP = _build_annual_aliases()


LEGACY_PAIR_LOOKUP = {
    (_normalize_icd(item.icd10), _label_key(item.label_is)): item
    for item in LEGACY_SERIES
}
