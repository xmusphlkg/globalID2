"""Conservative, source-aware review rules for newly onboarded national feeds.

These rules identify diseases; they do not authorize redistribution or change
observation periods, measures, cohorts, geography, or source definitions.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any


def normalized_label(label: str) -> str:
    text = unicodedata.normalize("NFKD", str(label)).casefold()
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^\w]+", " ", text).strip()


# Explicit translations only. Unrecognized/OCR-combined labels remain held.
_TRANSLATIONS = {
    "D002": ["Cólera", "COLERA", "อหิวาตกโรค"],
    "D004": ["Coronavirus Disease (COVID-19)"],
    "D005": ["SIDA"],
    "D007": ["ไวรัสตับอักเสบ เอ"],
    "D011": ["ไวรัสตับอักเสบ อี"],
    "D013": ["Polio", "โปลิโอ"],
    "D017": ["Sarampión", "campak", "Mazelen", "ไข้หัด"],
    "D019": ["Rabia", "Rabia humana", "Rabia humana silvestre", "Rabies (human)", "Rabiës", "พิษสุนัขบ้า"],
    "D020": ["Japanese B encephalitis", "Japanese B encephalitis, J.E.", "ไข้สมองอักเสบเจแปนนิส"],
    "D021": ["Dengue Fever", "Dengue fever", "ไข้เด็งกี"],
    "D023": ["Carbunco", "Antrax", "Ántrax (carbunco)", "แอนแทรกซ์"],
    "D025": ["Tuberkulosis", "TBC"],
    "D026": ["Fiebre Tifoidea y Paratifoidea", "Enteric fever (typhoid or paratyphoid fever)", "Enteric fever (typhoid/paratyphoid fever)"],
    "D028": ["Coqueluche", "Coqueluche (tos convulsa)", "Tos ferina", "Kinkhoest", "ไอกรน"],
    "D029": ["Difteria", "Difteri", "Difterie", "คอตีบ"],
    "D030": ["Tetanus neonatorum", "Tétanos neonatal", "TN", "บาดทะยักในเด็กแรกเกิด"],
    "D031": ["ไข้ดำแดง"],
    "D032": ["Brucelosis", "Brucellose", "บรูเซลโลสิส"],
    "D033": ["Gonorrhoea", "Infección gonocócica", "หนองใน"],
    "D034": ["Sífilis", "ซิฟิลิส"],
    "D035": ["Leptospirose", "เลปโตสไปโรสิส"],
    "D036": ["Bilharzia (schistosomiasis)"],
    "D037": ["ไข้มาลาเรีย"],
    "D038": ["Influenza Flu", "Influenza, (Flu)", "ไข้หวัดใหญ่"],
    "D039": ["Parotiditis", "Bof", "คางทูม"],
    "D042": ["Lepra", "Kusta", "เรื้อน"],
    "D045": ["Hidatidosis", "Hidatidosis (Equinococosis)"],
    "D046": ["Lymphatic Filariais", "เท้าช้าง"],
    "D048": ["Hand foot and mouth disease", "Hand,foot and mouth disease", "มือเท้าปาก"],
    "D049": ["MERS-CoV"],
    "D050": ["Ebola virus (VHF)"],
    "D051": ["Zika", "Zika virus", "ติดเชื้อไวรัสซิกา"],
    "D052": ["Fiebre Chikungunya", "Chikungunya fever", "Chikungunya virus", "ชิคุนกุนยา"],
    "D054": ["Varicela individual", "Chickenpox", "Varicella Chickenpox", "Varicella, Chickenpox", "สุกใส"],
    "D056": ["Streptococcus suis infection", "Streptococcus suis", "ไข้หูดับ"],
    "D058": ["สครับไทฟัส"],
    "D059": ["Fiebre amarilla", "Fiebre amarilla humana", "Fiebre amarilla selvática", "Gele Koorts"],
    "D064": ["Pokken"],
    "D065": ["Monkeypox", "Monkey pox", "Viruela símica (mpox)"],
    "D067": ["Acute hepatitis A Virus"],
    "D068": ["Acute hepatitis B Virus", "Hepatitis B Acuut", "ไวรัสตับอักเสบ บี เฉียบพลัน"],
    "D082": ["Leishmaniasis", "ลิซมาเนีย"],
    "D084": ["Chagas", "Enfermedad de Chagas"],
    "D088": ["Cisticercosis"],
    "D091": ["Botulismo", "Botulisme", "โบทูลิซึม"],
    "D093": ["Chancro blando", "แผลริมอ่อน"],
    "D102": ["Hantavirosis", "Orthohantavirusinfectie"],
    "D105": ["Shigellose", "Disenteria bacilar", "Disenteria bacilar (Shigelosis)", "บิดจากเชื้อชิเกลลา"],
    "D106": ["Invasieve pneumokokkenziekte"],
    "D107": ["Legionelosis", "Legionellose", "ลีเจียนแนร์"],
    "D108": ["Listeriose"],
    "D110": ["Enfermedad meningocócica", "Meningokokkenziekte", "Meningococcal meningitis", "Meningitis meningococcica", "ไข้กาฬหลังแอ่น"],
    "D112": ["Psitacosis"],
    "D113": ["Fiebre Q", "FiebreQ", "Q-koorts"],
    "D116": ["เมลิออยโดสิส"],
    "D120": ["Tétanos", "บาดทะยัก"],
    "D122": ["Triquinosis", "Trichinosis", "Trichinose", "Trichinosis, Trichinellosis, Trichiniasis", "ทริคิโนสิส"],
    "D123": ["Tularemie"],
    "D124": ["Fiebre tifoidea", "Typhoid", "Buiktyfus", "ไข้ไทฟอยด์"],
    "D127": ["Virale hemorragische koorts", "Viral haemorrhagic fever diseases"],
    "D128": ["Fiebre del Nilo Occidental", "West-Nilevirusinfectie", "West Nile virus"],
    "D135": ["Meningitis Bacteriana"],
    "D142": ["Respiratory Syncytial Virus", "Respiratory Syncytial Virus (RSV)", "ติดเชื้อไวรัสอาร์เอสวี"],
    "D144": ["Creutzfeldt-Jakob", "Ziekte van Creutzfeldt-Jakob (voorheen Klassiek)"],
    "D162": ["HIV", "VIH"],
    "D164": ["Síndrome Pulmonar por Hantavirus"],
    "D165": ["Amibiasis", "Amibiasis intestinal", "Amebiasis intestinal", "บิดจากเชื้ออะมีบา"],
    "D167": ["Sífilis congénita", "Congenital syphilis"],
    "D168": ["ไข้หัดเยอรมันแต่กำเนิด"],
    "D174": ["Marburg virus (VHF)"],
    "D178": ["AFP", "Acute Flaccid Paralysis (AFP)", "Acute Flaccid Paralysis (APF)"],
    "D197": ["Sífilis en embarazadas", "Sífilis en población gestante", "Sifilis gestacional"],
    "D198": ["Toxoplasmosis en gestantes"],
    "D203": ["Crimean-congo viral haemorrhagic fever (human)"],
    "D205": ["Tekenencefalitis"],
    "D208": ["Hepatitis B Chronisch"],
    "D210": ["Acute hepatitis C Virus", "Hepatitis C Acuut", "ไวรัสตับอักเสบ ซี เฉียบพลัน"],
    "D211": ["Hepatitis C Chronisch"],
    "D234": ["Paratyphoid", "Paratyfus A", "Paratyfus B", "Paratyfus C", "ไข้พาราไทฟอยด์"],
    "D235": ["Cerebrospinal Meningitis", "Cerebrospinal Meningitis (CSM)", "CSM"],
}

_NARROWER_LABELS = {
    "D021": ["DBD", "Dengue grave", "Dengue hemorrágico", "Dengue clásico", "Dengue con signos de alarma", "Dengue sin signos de alarma", "Dengue con señales de alarma", "Dengue sin señales de alarma", "Dengue haemorrhagic fever", "Dengue haemorrhagic fever (DHF)", "Dengue shock syndrome (DSS)", "ไข้เลือดออก", "ไข้เลือดออกช็อก"],
    "D025": ["Tuberculosis pulmonar", "Tuberculosis extra pulmonar", "Tuberculosis: pulmonary", "Tuberculosis: extra-pulmonary", "Pulmonary T.B."],
    "D082": ["Leishmaniasis cutánea", "Leishmaniasis mucosa", "Leishmaniasis mucocutánea"],
    "D091": ["Botulismo Adulto", "Botulismo Infantil", "Botulismo del lactante"],
    "D144": ["Ziekte van Creutzfeldt-Jakob - Variant"],
    "D120": ["Tétanos otras edades", "Tétanos accidental", "Tetanus exc.Neo"],
    "D197": ["Sífilis en emb."],
    "D100": ["Invasieve Haemophilus influenzae type b-infectie", "Haemophilus influenzae type B"],
    "D234": ["Paratyfus A", "Paratyfus B", "Paratyfus C"],
    "D110": ["Meningococcal meningitis", "Meningitis meningococcica"],
    "D235": ["Cerebrospinal Meningitis", "Cerebrospinal Meningitis (CSM)", "CSM"],
    "D037": ["Malaria P. Falciparum", "Malaria por P. Falciparum", "Malaria falciparum", "Malaria por P. Vivax", "Malaria vivax", "Malaria mixta", "Malaria asociada (formas mixtas)", "Malaria complicada"],
}


def review_target(category: dict[str, Any], concepts: list[dict[str, Any]]) -> tuple[str, str] | None:
    """Return a reviewed concept and relation only for explicit known labels."""
    names: dict[str, tuple[str, str]] = {}
    for concept in concepts:
        names[normalized_label(concept["labels"]["en"])] = (concept["id"], "exact")
    # Prefer the direct canonical names (e.g. D103, not duplicate HUS alias D151).
    names[normalized_label("Hemolytic uremic syndrome")] = ("D103", "exact")
    for target, labels in _TRANSLATIONS.items():
        names.update({normalized_label(label): (target, "exact") for label in labels})
    for target, labels in _NARROWER_LABELS.items():
        names.update({normalized_label(label): (target, "narrower") for label in labels})
    return names.get(normalized_label(category["canonical_source_label"]))


def projection_decision(category: dict[str, Any]) -> tuple[str, str]:
    """Fail closed on source-specific measures that cannot form public case curves."""
    source = category["source_id"]
    code = category["source_code"]
    definition = category["definition_version"]
    if source == "SRC_CO_INS_SIVIGILA":
        return "no_projection", "Municipality event summaries; national denominator and official week bounds unverified."
    if source == "SRC_AR_BEN":
        return "no_projection", "Cumulative publication snapshots; not single-week incident case counts."
    if source == "SRC_TH_ID_TH_ARCHIVE":
        return "no_projection", "Historical week calendar unverified; synthetic year-anchor dates cannot become weekly curves."
    if source == "SRC_MY_WHO_FLUNET":
        return "no_projection", "Laboratory detections and overlapping subtypes/origins; not national patient case counts."
    if source == "SRC_ID_KEMKES_PROFILE" and "appendix" in code:
        return "no_projection", "Appendix column/cohort counts remain separate; no reviewed national all-population total selector."
    if source == "SRC_MX_DGE_ANNUAL" and not code.endswith("__all__all__cases"):
        return "no_projection", "Only all-sex all-age case notifications are eligible; rates and subgroup cells remain separate."
    if source == "SRC_PE_CDC" and "__weekly_cases" not in code:
        return "no_projection", "Only verified single-week case measures selected; cumulative/death publication snapshots remain held."
    if source == "SRC_NG_NCDC" and ":weekly:" not in code:
        return "no_projection", "Cumulative/season publication snapshots are not single-week incident counts."
    if source == "SRC_ZA_NICD_NMC" and (":comparison_" in code or ":all_notified:" not in code):
        return "no_projection", "Comparison windows and classification/vital-status cohorts are not an additive national total."
    if source == "SRC_AR_BEN" and "laboratory" in definition:
        return "no_projection", "Cumulative laboratory tests/positives retain their distinct source measure."
    return "canonical", "Direct source series only; preserve metric, frequency, classification, geography and missing values; no automatic rollup."


__all__ = ["normalized_label", "review_target", "projection_decision"]
