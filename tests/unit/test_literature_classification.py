import json
from pathlib import Path

from src.literature.classification import (
    apply_surveillance_relation,
    classify_candidate,
    surveillance_relation_score_for_level,
)
from src.literature.pipeline import _global_country_catalogue
from src.literature.types import ArticleCandidate


ROOT = Path(__file__).resolve().parents[2]
TAXONOMY = json.loads(
    (ROOT / "configs/literature/taxonomy.json").read_text(encoding="utf-8")
)


def test_global_research_country_catalogue_does_not_depend_on_local_surveillance_rows():
    countries = _global_country_catalogue([], TAXONOMY)
    by_code = {row["code"]: row for row in countries}

    assert len(countries) >= 249
    assert by_code["CD"]["name_en"] == "Democratic Republic of the Congo"
DISEASES = [
    {
        "disease_id": "D021",
        "name_en": "Dengue",
        "name_zh": "登革热",
        "aliases": ["DENV", "dengue fever"],
    },
    {
        "disease_id": "D028",
        "name_en": "Pertussis",
        "name_zh": "百日咳",
        "aliases": ["Bordetella pertussis", "whooping cough"],
    },
    {
        "disease_id": "D050",
        "name_en": "Ebola",
        "name_zh": "埃博拉出血热",
        "aliases": ["Ebola virus disease"],
    },
]
COUNTRIES = [
    {"code": "CD", "name": "刚果民主共和国", "name_en": "Democratic Republic of the Congo"},
    {"code": "CG", "name": "刚果共和国", "name_en": "Republic of the Congo"},
    {"code": "US", "name": "美国", "name_en": "United States"},
    {"code": "JP", "name": "日本", "name_en": "Japan"},
]


def _candidate(
    title: str,
    *,
    abstract: str | None = None,
    source_payload: dict | None = None,
) -> ArticleCandidate:
    return ArticleCandidate(
        article_id="lit-test",
        slug="lit-test",
        title=title,
        abstract_text=abstract,
        source_payload=source_payload or {},
    )


def _classify(candidate: ArticleCandidate):
    return classify_candidate(
        candidate,
        diseases=DISEASES,
        countries=COUNTRIES,
        taxonomy=TAXONOMY,
    )


def test_stage_two_uses_controlled_europe_pmc_metadata_with_traceable_evidence():
    result = _classify(_candidate(
        "A multicentre infection study",
        source_payload={
            "europe_pmc": {
                "meshHeadingList": {
                    "meshHeading": [
                        {"descriptorName": "Dengue", "majorTopic_YN": "Y"},
                    ],
                },
                "keywordList": {"keyword": ["Japan", "genomic surveillance"]},
                "pubTypeList": {"pubType": ["Meta-Analysis"]},
            },
        },
    ))

    assert [match.key for match in result.diseases] == ["D021"]
    assert [match.key for match in result.countries] == ["JP"]
    assert {match.key for match in result.topics} >= {"Surveillance", "Genomic epidemiology"}
    assert result.study_type == "Meta-analysis"
    assert result.diseases[0].confidence >= 0.9
    assert any(
        term.startswith("semantic:europe_pmc.mesh[")
        for term in result.diseases[0].terms
    )
    assert any("europe_pmc.keyword" in term for term in result.countries[0].terms)


def test_stage_two_uses_openalex_topics_and_rejects_low_score_concepts():
    accepted = _classify(_candidate(
        "A population study",
        source_payload={
            "openalex": {
                "topics": [
                    {
                        "display_name": "Pertussis epidemiology and vaccination",
                        "score": 0.92,
                    },
                ],
                "keywords": [
                    {"display_name": "United States", "score": 0.81},
                ],
                "concepts": [
                    {"display_name": "Dengue", "score": 0.40},
                ],
            },
        },
    ))

    assert [match.key for match in accepted.diseases] == ["D028"]
    assert [match.key for match in accepted.countries] == ["US"]
    assert any("openalex.topic" in term for term in accepted.diseases[0].terms)
    assert "D021" not in {match.key for match in accepted.diseases}

    rejected = _classify(_candidate(
        "A population study",
        source_payload={
            "openalex": {
                "concepts": [{"display_name": "Dengue", "score": 0.40}],
            },
        },
    ))
    assert rejected.diseases == []


def test_country_aliases_disambiguate_the_two_congos():
    drc = _classify(_candidate(
        "Ebola response in the Democratic Republic of the Congo",
    ))
    brazzaville = _classify(_candidate(
        "Ebola preparedness in Congo-Brazzaville",
    ))

    assert [match.key for match in drc.countries] == ["CD"]
    assert [match.key for match in brazzaville.countries] == ["CG"]
    assert drc.countries[0].confidence >= 0.82
    assert "lexical:title:Democratic Republic of the Congo" in drc.countries[0].terms

    cross_border = _classify(_candidate(
        "Ebola coordination between the Republic of the Congo and the Democratic Republic of the Congo",
    ))
    assert {match.key for match in cross_border.countries} == {"CD", "CG"}

    semantic_drc = _classify(_candidate(
        "A response evaluation",
        source_payload={
            "europe_pmc": {
                "meshHeadingList": {"meshHeading": [{"descriptorName": "Ebola"}]},
                "keywordList": {"keyword": ["Democratic Republic of the Congo"]},
            },
        },
    ))
    assert [match.key for match in semantic_drc.countries] == ["CD"]


def test_short_country_acronyms_are_case_sensitive():
    lower_case_pronoun = _classify(_candidate(
        "Dengue vaccination helps us understand immune responses",
    ))
    upper_case_country = _classify(_candidate(
        "Dengue surveillance in the US",
    ))

    assert lower_case_pronoun.countries == []
    assert [match.key for match in upper_case_country.countries] == ["US"]
    assert "lexical:title:US" in upper_case_country.countries[0].terms

    semantic_pronoun = _classify(_candidate(
        "Dengue immune response",
        source_payload={
            "europe_pmc": {"keywordList": {"keyword": ["us"]}},
        },
    ))
    assert semantic_pronoun.countries == []


def test_iso_alpha3_aliases_extend_to_the_active_global_country_catalogue():
    result = _classify(_candidate("Dengue surveillance in JPN"))

    assert [match.key for match in result.countries] == ["JP"]
    assert result.countries[0].terms == ["lexical:title:JPN"]


def test_discovery_score_reserves_fifteen_percent_for_surveillance_relation():
    candidate = _candidate("Dengue surveillance in Japan")
    candidate.open_access_status = "open"
    unlinked = classify_candidate(
        candidate,
        diseases=DISEASES,
        countries=COUNTRIES,
        taxonomy=TAXONOMY,
        surveillance_relation_score=0.0,
    )
    exact = classify_candidate(
        candidate,
        diseases=DISEASES,
        countries=COUNTRIES,
        taxonomy=TAXONOMY,
        surveillance_relation_score=1.0,
    )

    assert unlinked.surveillance_relation_score == 0.0
    assert unlinked.discovery_score <= 0.85
    assert round(exact.discovery_score - unlinked.discovery_score, 3) == 0.15
    assert unlinked.discovery_score_components["surveillance_relation"] == {
        "value": 0.0,
        "weight": 0.15,
        "contribution": 0.0,
    }


def test_gap_relation_levels_fill_a_traceable_ordered_score_component():
    base = _classify(_candidate("Dengue surveillance in Japan"))
    base_score = base.discovery_score

    assert surveillance_relation_score_for_level("exact_disease_geography") == 1.0
    assert surveillance_relation_score_for_level("disease_context") == 0.6
    assert surveillance_relation_score_for_level("candidate") == 0.25
    assert surveillance_relation_score_for_level("unsupported") == 0.0

    apply_surveillance_relation(base, "disease_context")

    assert base.surveillance_relation_level == "disease_context"
    assert base.surveillance_relation_score == 0.6
    assert base.discovery_score == round(base_score + 0.09, 3)
    assert base.discovery_score_components["surveillance_relation"] == {
        "value": 0.6,
        "weight": 0.15,
        "contribution": 0.09,
    }


def test_small_classification_gold_set_meets_precision_and_recall_gate():
    fixtures = [
        (
            _candidate("Dengue surveillance in Japan"),
            {"disease:D021", "country:JP"},
        ),
        (
            _candidate("Ebola outbreak response in DRC"),
            {"disease:D050", "country:CD"},
        ),
        (
            _candidate("Ebola preparedness in the Republic of Congo"),
            {"disease:D050", "country:CG"},
        ),
        (
            _candidate("Dengue vaccination helps us understand immunity"),
            {"disease:D021"},
        ),
        (
            _candidate("Dengue surveillance in the U.S."),
            {"disease:D021", "country:US"},
        ),
        (
            _candidate(
                "A multicentre infection study",
                source_payload={
                    "europe_pmc": {
                        "meshHeadingList": {
                            "meshHeading": [{"descriptorName": "Dengue"}],
                        },
                        "keywordList": {"keyword": ["Japan"]},
                    },
                },
            ),
            {"disease:D021", "country:JP"},
        ),
        (
            _candidate(
                "A population study",
                source_payload={
                    "openalex": {
                        "topics": [
                            {"display_name": "Pertussis epidemiology", "score": 0.91},
                        ],
                    },
                },
            ),
            {"disease:D028"},
        ),
        (
            _candidate(
                "Orbital mechanics of distant galaxies",
                abstract="This astronomy paper helps us model stellar motion.",
            ),
            set(),
        ),
        (
            _candidate(
                "A population study",
                source_payload={
                    "openalex": {
                        "concepts": [{"display_name": "Dengue", "score": 0.31}],
                    },
                },
            ),
            set(),
        ),
    ]

    true_positive = false_positive = false_negative = 0
    for candidate, expected in fixtures:
        result = _classify(candidate)
        actual = {
            *{f"disease:{match.key}" for match in result.diseases},
            *{f"country:{match.key}" for match in result.countries},
        }
        true_positive += len(actual & expected)
        false_positive += len(actual - expected)
        false_negative += len(expected - actual)

    precision = true_positive / max(1, true_positive + false_positive)
    recall = true_positive / max(1, true_positive + false_negative)
    assert precision >= 0.95
    assert recall >= 0.90


def test_controlled_pathogen_population_and_domain_entities_are_traceable():
    result = _classify(_candidate(
        "Bordetella pertussis vaccination in pregnant women and infants",
        abstract="A clinical public health cohort of patients in Japan.",
    ))

    assert {match.key for match in result.pathogens} >= {"ncbi:520"}
    assert {match.key for match in result.pathogen_types} >= {"bacterium"}
    assert {match.key for match in result.populations} >= {"pregnant_women", "infants"}
    assert result.research_domain == "human_health"
    assert any(term.startswith("lexical:") for term in result.pathogens[0].terms)


def test_non_human_research_domains_cannot_cross_the_automatic_publish_gate():
    plant = _classify(_candidate(
        "Dengue-like plant disease in an agricultural crop",
        abstract="A phytopathogen study of a botanical host.",
    ))
    animal = _classify(_candidate(
        "Ebola infection in laboratory mice",
        abstract="A veterinary animal challenge study.",
    ))
    one_health = _classify(_candidate(
        "One Health surveillance of Ebola spillover in animals and people",
    ))

    assert plant.research_domain == "plant_only"
    assert plant.publication_status == "excluded"
    assert animal.research_domain == "animal_only"
    assert animal.publication_status == "review"
    assert one_health.research_domain == "one_health"


def test_pathogen_species_aliases_do_not_merge_candida_species():
    auris = _classify(_candidate("Candida auris outbreak investigation"))
    albicans = _classify(_candidate("Candida albicans infection study"))

    assert {match.key for match in auris.pathogens} == {"pathogen:candida-auris"}
    assert "ncbi:5476" not in {match.key for match in auris.pathogens}
    assert {match.key for match in albicans.pathogens} == {"ncbi:5476"}


def test_vaccine_safety_is_a_distinct_traceable_topic():
    result = _classify(_candidate(
        "Vaccine safety surveillance after dengue immunization",
        abstract="We measured adverse events following immunization in children.",
    ))

    topics = {match.key: match for match in result.topics}
    assert "Vaccine safety" in topics
    assert any("vaccine safety" in term.lower() for term in topics["Vaccine safety"].terms)


def test_ecological_study_is_detected_lexically_and_from_controlled_pub_type():
    lexical = _classify(_candidate(
        "An ecological study of dengue incidence and rainfall in Japan",
    ))
    semantic = _classify(_candidate(
        "Population patterns of dengue in Japan",
        source_payload={
            "europe_pmc": {
                "pubTypeList": {"pubType": ["Ecological Study"]},
            },
        },
    ))

    assert lexical.study_type == "Ecological study"
    assert semantic.study_type == "Ecological study"
