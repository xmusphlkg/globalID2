"""
Schema-first, source-grounded brief generation.

The first implementation is deterministic and conservative. It produces a
publishable schema from verified source rows without copying substantial source
text. The same schema can later be filled by an LLM, with the reviewer enforcing
the same source-grounding rules.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


DISCLAIMER_EN = (
    "This brief is for surveillance and public information only. "
    "It is not medical advice, diagnosis, or treatment guidance."
)
DISCLAIMER_ZH = "本简介仅用于监测与公共信息展示，不构成医疗建议、诊断或治疗指导。"


@dataclass(frozen=True)
class BriefValidationResult:
    ok: bool
    issues: list[str]


class SourceGroundedBriefGenerator:
    """Generate bilingual disease briefs from short source records."""

    PUBLIC_SOURCE_TYPES = {"who", "who_don", "wikidata", "wikipedia"}
    AUTHORITATIVE_SOURCE_TYPES = {"who", "who_don"}

    def generate(
        self,
        *,
        disease: dict[str, Any],
        sources: list[dict[str, Any]],
        language: str,
    ) -> dict[str, Any]:
        language = "zh" if language == "zh" else "en"
        usable_sources = self._usable_sources(sources)
        source_ids = [src.get("id") for src in usable_sources if src.get("id") is not None]
        source_types = {str(src.get("source_type") or "") for src in usable_sources}
        source_confidence = self._source_confidence(source_types)
        status = "published" if usable_sources and source_types != {"msd"} else "requires_review"

        name_en = disease.get("name_en") or disease.get("standard_name_en") or disease.get("disease_id")
        name_zh = disease.get("name_zh") or disease.get("standard_name_zh") or name_en
        display_name = name_zh if language == "zh" else name_en
        category = disease.get("category") or "infectious disease"
        description = self._short_description(disease, language)
        attribution = self._source_attribution(usable_sources)
        source_digest = self._source_digest(usable_sources)
        clinical_anchor = self._clinical_anchor(usable_sources, language)
        epidemiology_anchor = self._epidemiology_anchor(usable_sources, language)
        transmission_anchor = self._transmission_anchor(usable_sources, language)
        prevention_anchor = self._prevention_anchor(usable_sources, language)
        surveillance_anchor = self._surveillance_anchor(usable_sources, language)
        risk_groups_anchor = self._risk_groups_anchor(usable_sources, language)
        source_sentence_en = (
            f" Source snippets describe the disease as follows: {source_digest}."
            if source_digest and language != "zh"
            else ""
        )
        source_sentence_zh = (
            f" 来源摘要显示：{source_digest}。"
            if source_digest and language == "zh"
            else ""
        )

        if language == "zh":
            definition = (
                f"{display_name}是一种纳入传染病监测目录的{self._category_zh(category)}，"
                f"其标准目录描述为：{description}"
            )
            if source_digest:
                definition += f"。来源摘要进一步说明：{source_digest}"
            clinical_features = (
                f"{display_name}的临床意义取决于病原体、病程严重度、感染部位以及当地病例定义。"
                f"{clinical_anchor}"
            )
            epidemiology = (
                f"{display_name}的流行病学解读应结合地理分布、动物宿主或人群暴露背景、暴发事件以及报告频率。"
                f"{epidemiology_anchor}"
            )
            transmission = transmission_anchor
            prevention = prevention_anchor
            surveillance_note = surveillance_anchor
            risk_groups = risk_groups_anchor
            brief = (
                f"{definition} {epidemiology} {surveillance_note}"
            )
            clinical_summary = clinical_features
            disclaimer = DISCLAIMER_ZH
        else:
            definition = (
                f"{display_name} is a {str(category).lower()} infectious disease tracked in the GlobalID surveillance catalogue. "
                f"The catalogue description states: {description}"
            )
            if source_digest:
                definition += f" Source snippets further indicate: {source_digest}"
            clinical_features = (
                f"The clinical significance of {display_name} depends on pathogen characteristics, severity pattern, site of infection, and local case definitions. "
                f"{clinical_anchor}"
            )
            epidemiology = (
                f"The epidemiology of {display_name} should be read in relation to geography, animal reservoirs or exposure ecology, outbreak activity, and reporting cadence. "
                f"{epidemiology_anchor}"
            )
            transmission = transmission_anchor
            prevention = prevention_anchor
            surveillance_note = surveillance_anchor
            risk_groups = risk_groups_anchor
            brief = (
                f"{definition} {epidemiology} {surveillance_note}"
            )
            clinical_summary = clinical_features
            disclaimer = DISCLAIMER_EN

        payload = {
            "disease_id": disease.get("disease_id"),
            "language": language,
            "brief": self._clean(brief),
            "definition": self._clean(definition),
            "clinical_features": self._clean(clinical_features),
            "epidemiology": self._clean(epidemiology),
            "clinical_summary": self._clean(clinical_summary),
            "transmission": self._clean(transmission),
            "prevention": self._clean(prevention),
            "surveillance_note": self._clean(surveillance_note),
            "risk_groups": self._clean(risk_groups),
            "source_ids": source_ids,
            "source_attribution": attribution,
            "disclaimer": disclaimer,
            "model": "source-grounded-template-v1",
            "status": status,
            "source_confidence": source_confidence,
            "quality_score": 0.88 if status == "published" else 0.55,
            "review_notes": self._review_notes(status, source_types, language),
            "metadata": {
                "source_types": sorted(source_types),
                "generator": "SourceGroundedBriefGenerator",
                "version": 1,
            },
        }
        validation = self.validate(payload)
        if not validation.ok:
            payload["status"] = "requires_review"
            payload["quality_score"] = min(float(payload.get("quality_score") or 0.5), 0.5)
            payload["review_notes"] = "; ".join(validation.issues)
        return payload

    def validate(self, payload: dict[str, Any]) -> BriefValidationResult:
        issues: list[str] = []
        if not payload.get("brief"):
            issues.append("missing brief")
        for key in ("definition", "clinical_features", "epidemiology", "transmission", "prevention", "surveillance_note", "risk_groups"):
            if not payload.get(key):
                issues.append(f"missing {key}")
        if payload.get("language") not in {"en", "zh"}:
            issues.append("invalid language")
        if not payload.get("source_ids"):
            issues.append("missing source_ids")
        if not payload.get("source_attribution"):
            issues.append("missing source_attribution")
        if "medical advice" not in str(payload.get("disclaimer", "")).lower() and "医疗建议" not in str(
            payload.get("disclaimer", "")
        ):
            issues.append("missing public-information disclaimer")
        return BriefValidationResult(ok=not issues, issues=issues)

    def _usable_sources(self, sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
        approved = [
            src
            for src in sources
            if str(src.get("source_type") or "") in self.PUBLIC_SOURCE_TYPES
            and str(src.get("review_status") or "pending") != "rejected"
        ]
        if approved:
            return approved[:6]
        msd_only = [src for src in sources if str(src.get("source_type") or "") == "msd"]
        return msd_only[:2]

    def _source_confidence(self, source_types: set[str]) -> str:
        if source_types & self.AUTHORITATIVE_SOURCE_TYPES:
            return "high"
        if source_types & {"wikidata", "wikipedia"}:
            return "medium"
        return "low"

    @staticmethod
    def _source_attribution(sources: list[dict[str, Any]]) -> list[dict[str, str | None]]:
        attribution = []
        for src in sources:
            attribution.append(
                {
                    "id": src.get("id"),
                    "source_name": src.get("source_name"),
                    "source_type": src.get("source_type"),
                    "title": src.get("title"),
                    "url": src.get("url"),
                    "resolved_url": src.get("resolved_url") or src.get("url"),
                    "license": src.get("license"),
                }
            )
        return attribution

    def _source_digest(self, sources: list[dict[str, Any]]) -> str:
        snippets: list[str] = []
        for src in sources:
            if str(src.get("source_type") or "") == "msd":
                continue
            excerpt = (
                src.get("content_text")
                or src.get("raw_excerpt")
                or src.get("snippet")
                or src.get("description")
            )
            if excerpt:
                snippets.append(self._clean(str(excerpt)))
            if len(snippets) >= 3:
                break
        digest = " ".join(snippets)
        if len(digest) > 720:
            return digest[:720].rstrip() + "..."
        return digest

    def _clinical_anchor(self, sources: list[dict[str, Any]], language: str) -> str:
        digest = self._source_digest(sources)
        digest_lower = digest.lower()
        if not digest:
            return (
                "Source-backed clinical detail is not yet available; the page should be interpreted at the surveillance level."
                if language != "zh"
                else "当前源片段尚不足以形成完整临床特征描述；页面应优先作为监测解读使用。"
            )
        if any(keyword in digest_lower for keyword in ("bubonic", "pneumonic", "septicemic", "septic", "bubo", "lymph node", "lymphadenitis")):
            if language == "zh":
                return (
                    "来源片段提示该病种具有可区分的临床分型或综合征特征，可能涉及淋巴结肿大、局部化感染、败血症表现或肺部受累；"
                    "具体表型仍应以原始来源为准。"
                )
            return (
                "The source material points to distinguishable clinical syndromes or forms, including lymph node involvement, localized infection, septic manifestations, or pulmonary disease; the exact phenotype should still be read from the cited source."
            )
        if any(keyword in digest_lower for keyword in ("fatal", "severe", "rapidly", "shock", "respiratory failure")):
            if language == "zh":
                return "来源片段显示该病种可出现快速进展或重症表现，因此临床解读应特别关注病程速度、并发症和治疗时机。"
            return "The source material suggests rapid progression or severe disease, so clinical interpretation should pay close attention to tempo, complications, and timing of treatment."
        if language == "zh":
            return (
                "源片段提示了病程严重度、临床分型或症状表现，但本站仅保留与监测解读相关的要点。"
            )
        return (
            "The source snippets point to severity patterns, clinical forms, or symptom clusters, but the site retains only the points needed for surveillance interpretation."
        )

    def _epidemiology_anchor(self, sources: list[dict[str, Any]], language: str) -> str:
        digest = self._source_digest(sources).lower()
        if any(keyword in digest for keyword in ("outbreak", "endemic", "pandemic", "epidemic", "countries", "regions", "cases")):
            if language == "zh":
                return "来源片段显示该病种与特定地区、暴发事件或持续性监测负担相关。"
            return "The source material links the disease to specific geographies, outbreaks, or an ongoing surveillance burden."
        if language == "zh":
            return "当前源片段未给出完整流行病学概述，页面仅保留与监测负担相关的解释。"
        return "The current source snippets do not provide a full epidemiologic profile, so the page retains only surveillance-relevant context."

    def _transmission_anchor(self, sources: list[dict[str, Any]], language: str) -> str:
        digest = self._source_digest(sources).lower()
        if any(keyword in digest for keyword in ("flea", "droplet", "respiratory", "water", "food", "animal", "vector", "contact")):
            if language == "zh":
                return "传播/暴露信息以可追溯来源为准；当前来源片段支持将该病种理解为与特定媒介、接触、呼吸道或动物暴露相关。"
            return "Transmission and exposure should follow the cited sources; the current snippets support a route involving specific vectors, contact exposure, respiratory spread, or animal reservoirs."
        if language == "zh":
            return "当前源片段未提供可核验的传播机制，因此页面不自动推断具体传播路径。"
        return "The current snippets do not provide a fully verifiable transmission mechanism, so the page does not infer one."

    def _prevention_anchor(self, sources: list[dict[str, Any]], language: str) -> str:
        digest = self._source_digest(sources).lower()
        if any(keyword in digest for keyword in ("antibiotic", "vaccin", "precaution", "isolation", "hygiene", "vector control", "early treatment")):
            if language == "zh":
                return "预防重点在于早期识别、及时治疗、标准防护、暴露后管理以及与传播途径相匹配的公共卫生措施；具体做法取决于来源所述场景。"
            return "Prevention centers on early recognition, timely treatment, standard precautions, post-exposure management, and public-health measures matched to the exposure setting; the exact package depends on the source-described scenario."
        if language == "zh":
            return "当前源片段未形成完整预防段落，因此页面仅保留监测层面的预防解读。"
        return "The current snippets do not support a full prevention paragraph, so the page retains only surveillance-level prevention context."

    def _surveillance_anchor(self, sources: list[dict[str, Any]], language: str) -> str:
        digest = self._source_digest(sources).lower()
        if any(keyword in digest for keyword in ("outbreak", "endemic", "surveillance", "cases", "fatal", "epidemic")):
            if language == "zh":
                return "监测解读应结合病例定义、报告频率、地区覆盖和报告延迟；若出现突增，需要先核实数据口径，再讨论公共卫生意义。"
            return "Surveillance interpretation should account for case definitions, reporting cadence, geographic coverage, and reporting lag; sudden increases should be checked against the data definition before drawing conclusions."
        if language == "zh":
            return "当前源片段未提供足够的监测背景，因此仅可将该页作为疾病背景索引。"
        return "The current source snippets do not provide enough surveillance background, so the page should be used primarily as a disease background index."

    def _risk_groups_anchor(self, sources: list[dict[str, Any]], language: str) -> str:
        digest = self._source_digest(sources).lower()

        if any(keyword in digest for keyword in ("children", "child", "infant", "pediatric", "adolescent")):
            return (
                "来源片段提示儿童或青少年可能是更值得关注的人群，尤其是在家庭传播、学校场景或免疫覆盖差异明显的背景下。"
                if language == "zh"
                else "The source material points to children or adolescents as a group of interest, especially where household transmission, school settings, or gaps in immunization coverage are relevant."
            )
        if any(keyword in digest for keyword in ("pregnan", "maternal", "neonate", "newborn", "perinatal")):
            return (
                "来源片段提示孕产妇、新生儿或围产期人群需要单独关注，因为结局严重度、暴露窗口和预防措施可能不同。"
                if language == "zh"
                else "The source material points to pregnant people, neonates, or perinatal populations as groups requiring separate attention because severity, exposure windows, and prevention strategies may differ."
            )
        if any(keyword in digest for keyword in ("health worker", "healthcare", "medical staff", "clinician", "laboratory", "laborator", "nurse")):
            return (
                "来源片段提示医护人员、实验室人员或其他职业暴露人群应纳入重点人群解读。"
                if language == "zh"
                else "The source material suggests that health workers, laboratory staff, or other occupationally exposed groups should be treated as key risk groups."
            )
        if any(keyword in digest for keyword in ("travel", "traveler", "traveller", "visitor", "migrant")):
            return (
                "来源片段提示旅行者、迁徙人群或跨地区流动人群值得关注，尤其是在输入性病例和跨境传播分析中。"
                if language == "zh"
                else "The source material suggests that travelers, mobile populations, or cross-border movement should be considered, particularly in imported-case and cross-jurisdiction analyses."
            )
        if any(keyword in digest for keyword in ("rodent", "flea", "animal", "livestock", "zoon", "carcass", "vector", "outdoor", "field")):
            return (
                "来源片段提示与动物宿主、媒介暴露、野外活动或接触动物尸体相关的人群值得优先关注。"
                if language == "zh"
                else "The source material suggests that people with animal-host exposure, vector exposure, outdoor activity, or carcass handling deserve priority attention."
            )
        if any(keyword in digest for keyword in ("immunocompromised", "immunosuppressed", "hiv", "elderly", "older adult", "older adults")):
            return (
                "来源片段提示免疫功能受损者或老年人可能需要更高关注，因为并发症和严重结局风险可能更高。"
                if language == "zh"
                else "The source material suggests that immunocompromised people or older adults may merit additional attention because complication and severe-outcome risk can be higher."
            )
        if language == "zh":
            return "若来源未明确指向特定重点人群，则应保留为待复核状态，而不是凭经验自动补写。"
        return "If the sources do not identify a specific risk group, the field should remain review-gated rather than being padded from general medical memory."

    @staticmethod
    def _short_description(disease: dict[str, Any], language: str) -> str:
        text = disease.get("description") or ""
        if not text:
            return "The source profile is maintained for surveillance interpretation." if language == "en" else "该条目用于传染病监测解读。"
        if language == "zh":
            return f"标准目录描述为：{text}。"
        return f"The standard catalogue describes it as: {text}."

    @staticmethod
    def _category_zh(category: str) -> str:
        mapping = {
            "Bacterial": "细菌性疾病",
            "Viral": "病毒性疾病",
            "Parasitic": "寄生虫病",
            "Fungal": "真菌性疾病",
            "Prion": "朊粒相关疾病",
            "Other": "疾病",
        }
        return mapping.get(str(category), "疾病")

    @staticmethod
    def _review_notes(status: str, source_types: set[str], language: str) -> str:
        if status == "published":
            return "Source-grounded brief generated from approved public sources." if language == "en" else "已基于可追溯公共来源生成。"
        if source_types == {"msd"}:
            return "MSD-only match; manual review required before publication." if language == "en" else "仅匹配 MSD 元数据，发布前需人工复核。"
        return "No authoritative source found; manual review required." if language == "en" else "未找到足够权威来源，需人工复核。"

    @staticmethod
    def _clean(text: str) -> str:
        return " ".join(str(text).split())
