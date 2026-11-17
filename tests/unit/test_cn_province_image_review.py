from copy import deepcopy
import hashlib
import io
import json
from unittest.mock import AsyncMock, Mock

import pytest

from src.ai.attachments import build_probe_pdf
from src.ai.peer_review import Completion
from src.services import cn_province_image_review as review


def document():
    data = build_probe_pdf("original")
    return {"data": data, "media_type": "application/pdf", "source_url": "https://official.example/report.pdf",
            "sha256": hashlib.sha256(data).hexdigest()}


def extraction(doc=None):
    sha = (doc or document())["sha256"]
    evidence = {"text": "北京市2026年9月传染病报告", "source": "document", "document_sha256": sha, "page": 1}
    return {
        "province_label": "北京市", "province_evidence": evidence,
        "report_month": "2026-09", "period_evidence": deepcopy(evidence),
        "headers": ["病种", "发病数", "死亡数"], "cases_header": "发病数", "deaths_header": "死亡数",
        "rows": [{"label": label, "category": "乙类", "parent_label": None, "cases": cases, "deaths": 0,
                  "cases_text": str(cases), "deaths_text": "0", "document_sha256": sha, "page": 1,
                  "bbox": None} for label, cases in (("甲型肝炎", 3), ("乙型肝炎", 4))],
        "totals": [{"label": "乙类合计", "category": "乙类", "member_labels": ["甲型肝炎", "乙型肝炎"],
                    "cases": 7, "deaths": 0, "cases_text": "7", "deaths_text": "0", "document_sha256": sha, "page": 1}],
        "footnotes": ["本表为报告病例数。"], "complete_table": True,
        "all_printed_totals_captured": True, "all_footnotes_captured": True, "uncertainties": [],
        "inspected_pages": [{"document_sha256": sha, "page": 1}],
    }


class Reader:
    """Stateless scripted experts; the production path creates separate histories."""
    def __init__(self, payloads=None, *, modes=None, models=None):
        if payloads is None:
            payloads = extraction()
        self.payloads = payloads if isinstance(payloads, list) else [deepcopy(payloads) for _ in range(3)]
        count = len(self.payloads)
        self.modes = modes or ["native_file"] * count
        self.models = models or [f"provider:vision-{index}" for index in range(count)]
        self.calls = []

    async def __call__(self, role, system, prompt, *, attachments):
        index = len(self.calls)
        self.calls.append({"role": role, "system": system, "prompt": prompt, "attachments": attachments})
        value = self.payloads[index]
        if isinstance(value, BaseException):
            raise value
        return Completion(text=value if isinstance(value, str) else json.dumps(value, ensure_ascii=False),
                          model=self.models[index], input_mode=self.modes[index])


async def run(reader=None, **kwargs):
    return await review.review_image_report(documents=kwargs.pop("documents", [document()]),
        province_code="CN-BJ", report_month="2026-09", completer=reader or Reader(), **kwargs)


def variant(value):
    payload = extraction()
    payload["rows"][0].update(cases=value, cases_text=str(value))
    payload["totals"][0].update(cases=value + 4, cases_text=str(value + 4))
    return payload


async def test_three_native_experts_vote_independently_and_preserve_reproducible_audit():
    reader = Reader()
    result = await run(reader)
    assert result["status"] == "validated"
    assert [row["cases"] for row in result["rows"]] == [3, 4]
    assert result["rows"][0]["footnotes"] == ["本表为报告病例数。"]
    audit = result["audit"]
    assert audit["panel_size"] == audit["winner_votes"] == 3
    assert audit["quorum"] == 2
    assert len(audit["readings"]) == 3
    assert all(slot["status"] == "voted" for slot in audit["readings"])
    assert {call["role"] for call in reader.calls} == {"author"}
    assert len({call["prompt"] for call in reader.calls}) == len({call["system"] for call in reader.calls}) == 1
    assert reader.calls[0]["attachments"][0].data == document()["data"]
    assert all(call["attachments"] is reader.calls[0]["attachments"] for call in reader.calls)
    assert audit["schema_sha256"] == review._digest(review.review_output_schema())
    assert audit["prompt_sha256"] == review._digest({key: reader.calls[0][key] for key in ("system", "prompt")})
    assert audit["extractor_config"]["injected_completer"] is True


async def test_two_of_three_whole_report_votes_win_without_adopting_dissenting_cells():
    reader = Reader([variant(3), variant(8), variant(3)])
    result = await run(reader)
    assert result["status"] == "validated" and result["rows"][0]["cases"] == 3
    assert result["audit"]["winner_votes"] == 2
    assert [vote["votes"] for vote in result["audit"]["vote_counts"]] == [2, 1]
    assert len(reader.calls) == 3


async def test_three_internally_valid_disagreeing_reports_have_no_majority():
    result = await run(Reader([variant(3), variant(4), variant(5)]))
    assert result["status"] == "needs_review" and result["rows"] == []
    assert result["audit"]["valid_votes"] == 3
    assert result["audit"]["winner_votes"] == 0


async def test_footnotes_are_part_of_the_whole_report_fingerprint():
    payloads = [extraction() for _ in range(3)]
    for index, item in enumerate(payloads):
        item["footnotes"] = [f"definition {index}"]
    assert (await run(Reader(payloads)))["status"] == "needs_review"


@pytest.mark.parametrize("failure", ["invalid_json", "unknown_model", "text_fallback", "runtime_failure", "schema_failure"])
async def test_invalid_expert_abstains_but_two_other_experts_can_reach_fixed_quorum(failure):
    payloads, modes, models = [extraction() for _ in range(3)], ["native_file"] * 3, ["p:a", "p:b", "p:c"]
    if failure == "invalid_json": payloads[0] = "not JSON"
    elif failure == "unknown_model": models[0] = "unknown"
    elif failure == "text_fallback": modes[0] = "text"
    elif failure == "runtime_failure": payloads[0] = RuntimeError("provider failed")
    else: payloads[0]["complete_table"] = False
    reader = Reader(payloads, modes=modes, models=models)
    result = await run(reader)
    assert result["status"] == "validated"
    assert result["audit"]["quorum"] == result["audit"]["winner_votes"] == 2
    assert result["audit"]["readings"][0]["status"] == "abstained"
    assert len(result["audit"]["readings"]) == len(reader.calls) == 3
    assert len({call["prompt"] for call in reader.calls}) == 1


async def test_two_valid_agreeing_votes_do_not_reduce_a_five_expert_panel_to_three():
    reader = Reader([extraction(), extraction(), "bad", "bad", "bad"])
    result = await run(reader, panel_size=5)
    assert result["status"] == "needs_review"
    assert result["audit"]["quorum"] == 3 and result["audit"]["valid_votes"] == 2
    assert len(result["audit"]["readings"]) == 5


async def test_two_two_tie_with_one_abstention_stays_pending():
    result = await run(Reader([variant(3), variant(4), variant(3), variant(4), "bad"]), panel_size=5)
    assert result["status"] == "needs_review"
    assert [vote["votes"] for vote in result["audit"]["vote_counts"]] == [2, 2]


async def test_seven_expert_panel_requires_four_votes():
    result = await run(Reader([variant(3)] * 4 + [variant(4)] * 3), panel_size=7)
    assert result["status"] == "validated"
    assert result["audit"]["winner_votes"] == result["audit"]["quorum"] == 4


async def test_same_model_on_different_providers_never_receives_additional_votes():
    result = await run(Reader(models=["a:SAME", "b:same", "c:same"]))
    assert result["status"] == "needs_review"
    assert [slot["status"] for slot in result["audit"]["readings"]] == ["voted", "abstained", "abstained"]
    assert result["audit"]["valid_votes"] == 1


async def test_one_duplicate_abstains_and_two_distinct_agreeing_models_still_meet_two_of_three():
    result = await run(Reader(models=["a:same", "b:same", "c:other"]))
    assert result["status"] == "validated" and result["audit"]["winner_votes"] == 2
    assert result["audit"]["readings"][1]["status"] == "abstained"


async def test_text_only_attempt_cannot_return_as_a_second_expert_for_the_same_model():
    result = await run(Reader(models=["a:same", "b:same", "c:other"], modes=["text", "native_file", "native_file"]))
    assert result["status"] == "needs_review" and result["audit"]["valid_votes"] == 1


async def test_two_invalid_experts_leave_one_valid_vote_below_fixed_three_seat_quorum():
    result = await run(Reader(["not JSON", {"unexpected": "schema"}, extraction()]))
    assert result["status"] == "needs_review" and result["rows"] == []
    assert result["audit"]["quorum"] == 2
    assert result["audit"]["valid_votes"] == 1
    assert [slot["status"] for slot in result["audit"]["readings"]] == ["abstained", "abstained", "voted"]


@pytest.mark.parametrize("panel_size", [0, 1, 2, 4, 6, 8, 9, True, "3"])
async def test_invalid_panel_sizes_fail_without_any_model_calls(panel_size):
    reader = Reader()
    assert (await run(reader, panel_size=panel_size))["status"] == "needs_review"
    assert not reader.calls


@pytest.mark.parametrize("bad", [-1, True, 3.0, "3", None])
async def test_count_schema_is_strict_and_missing_case_never_disappears(bad):
    payload = extraction()
    payload["rows"][0]["cases"] = bad
    assert (await run(Reader(payload)))["status"] == "needs_review"


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(extra="unrecognized"),
    lambda p: p.update(complete_table=False),
    lambda p: p.update(uncertainties=["one row illegible"]),
    lambda p: p.update(all_footnotes_captured=False),
    lambda p: p.update(inspected_pages=[]),
    lambda p: p["rows"][0].update(page=2),
    lambda p: p["rows"][0].update(document_sha256="f" * 64),
    lambda p: p["rows"][0].update(cases=0, cases_text=""),
    lambda p: p["rows"][0].update(label="甲型肝炎疑似"),
    lambda p: p["rows"][0].update(bbox=[0.8, 0, 0.1, 1]),
    lambda p: p["totals"][0].update(cases=8, cases_text="8"),
    lambda p: p["totals"][0].update(label="甲类合计"),
    lambda p: p["totals"][0].update(member_labels=["甲型肝炎"]),
    lambda p: p.update(totals=[]),
    lambda p: p.update(province_label="河北省"),
    lambda p: p["period_evidence"].update(text="北京市2026年8月传染病报告"),
    lambda p: p["period_evidence"].update(text="截至2026年9月累计报告"),
    lambda p: p.update(headers=["病种", "累计发病数", "死亡数"], cases_header="累计发病数"),
    lambda p: p.update(headers=["发病数", "死亡数"]),
    lambda p: p.update(headers=["病种", "发病数"], deaths_header=None),
    lambda p: p["rows"][0].update(parent_label="乙型肝炎"),
])
async def test_semantic_or_evidence_failures_require_review(mutation):
    payload = extraction()
    mutation(payload)
    result = await run(Reader(payload))
    assert result["status"] == "needs_review" and not result["rows"]
    assert result["audit"]["errors"]


async def test_alias_equivalence_is_deterministic_without_fuzzy_matching():
    other = extraction()
    other["rows"][0]["label"] = "甲肝"
    other["totals"][0]["member_labels"][0] = "甲肝"
    assert (await run(Reader([extraction(), other, other])))["status"] == "validated"


@pytest.mark.parametrize("overall_category,overall_label", [("甲乙丙类", "甲乙丙类合计"), ("全部", "总计")])
async def test_printed_combined_ab_section_is_preserved_and_counted_in_overall_totals(overall_category, overall_label):
    payload = extraction()
    for item in payload["rows"]:
        item["category"] = "甲乙类"
    payload["totals"][0].update(label="甲、乙类合计", category="甲乙类")
    category_c = {**deepcopy(payload["rows"][0]), "label": "流行性感冒", "category": "丙类", "cases": 5, "cases_text": "5"}
    payload["rows"].append(category_c)
    payload["totals"].extend([
        {**deepcopy(payload["totals"][0]), "label": "丙类合计", "category": "丙类",
         "member_labels": ["流行性感冒"], "cases": 5, "cases_text": "5"},
        {**deepcopy(payload["totals"][0]), "label": overall_label, "category": overall_category,
         "member_labels": ["甲型肝炎", "乙型肝炎", "流行性感冒"], "cases": 12, "cases_text": "12"},
    ])
    result = await run(Reader(payload))
    assert result["status"] == "validated"
    assert [item["category"] for item in result["rows"]] == ["甲乙类", "甲乙类", "丙类"]


async def test_registered_parent_and_member_aliases_resolve_to_present_disease_rows():
    payload = extraction()
    for item in payload["rows"]:
        item["parent_label"] = "肝炎"
        item["category"] = "甲乙类"
    parent = {**deepcopy(payload["rows"][0]), "label": "病毒性肝炎", "parent_label": None, "cases": 10, "cases_text": "10"}
    payload["rows"].insert(0, parent)
    payload["totals"][0].update(label="甲乙类合计", category="甲乙类", member_labels=["肝炎"], cases=10, cases_text="10")
    assert (await run(Reader(payload)))["status"] == "validated"
    payload["rows"][1]["parent_label"] = "未知肝炎"
    assert (await run(Reader(payload)))["status"] == "needs_review"


@pytest.mark.parametrize("fault", ["subtotal_member", "subtotal_parent", "separate_class", "duplicate_alias_member"])
async def test_combined_categories_never_allow_aggregate_members_or_invented_parentage(fault):
    payload = extraction()
    for item in payload["rows"]:
        item["category"] = "甲乙类"
    payload["totals"][0].update(label="甲乙类合计", category="甲乙类")
    if fault == "subtotal_member":
        payload["totals"][0]["member_labels"] = ["甲乙类合计"]
    elif fault == "subtotal_parent":
        payload["rows"][0]["parent_label"] = "甲乙类"
    elif fault == "separate_class":
        payload["totals"][0].update(label="甲类合计", category="甲类")
    else:
        payload["totals"][0]["member_labels"] = ["甲型肝炎", "甲肝", "乙型肝炎"]
    assert (await run(Reader(payload)))["status"] == "needs_review"


async def test_definition_start_boundary_is_enforced():
    payload = extraction()
    payload["report_month"] = "2026-03"
    for field in ("period_evidence", "province_evidence"):
        payload[field]["text"] = "北京市2026年3月传染病报告"
    payload["rows"][0]["label"] = "发热伴血小板减少综合征"
    payload["totals"][0]["member_labels"][0] = "发热伴血小板减少综合征"
    result = await review.review_image_report(documents=[document()], province_code="CN-BJ",
                                          report_month="2026-03", completer=Reader(payload))
    assert result["status"] == "needs_review"
    assert all("definition is not valid" in slot["reason"] for slot in result["audit"]["readings"])


async def test_partial_child_groups_only_check_upper_bound_without_inferring_remainder():
    payload = extraction()
    parent = {**deepcopy(payload["rows"][0]), "label": "病毒性肝炎", "cases": 10, "cases_text": "10"}
    for row in payload["rows"]:
        row["parent_label"] = "病毒性肝炎"
    payload["rows"].insert(0, parent)
    payload["totals"][0].update(cases=10, cases_text="10", member_labels=["病毒性肝炎"])
    result = await run(Reader(payload))
    assert result["status"] == "validated" and len(result["rows"]) == 3
    payload["rows"][0].update(cases=6, cases_text="6")
    payload["totals"][0].update(cases=6, cases_text="6")
    assert (await run(Reader(payload)))["status"] == "needs_review"


async def test_missing_deaths_stay_unknown_if_document_has_no_death_column():
    payload = extraction()
    payload.update(headers=["病种", "发病数"], deaths_header=None)
    for row in [*payload["rows"], *payload["totals"]]:
        row.update(deaths=None, deaths_text="")
    result = await run(Reader(payload))
    assert result["status"] == "validated"
    assert all(row["deaths"] is None for row in result["rows"])


async def test_json_prose_duplicate_keys_or_trailing_content_are_not_sanitized_into_acceptance():
    for text in ["```json\n" + json.dumps(extraction()) + "\n```", json.dumps(extraction()) + " ignored",
                 '{"province_label":"北京市", "province_label":"河北省"}']:
        assert (await run(Reader(text)))["status"] == "needs_review"


async def test_bad_bytes_digest_or_empty_documents_do_not_call_models():
    reader = Reader()
    bad = {**document(), "sha256": "0" * 64}
    assert (await run(reader, documents=[bad]))["status"] == "needs_review"
    assert (await run(reader, documents=[]))["status"] == "needs_review"
    assert not reader.calls


async def test_original_image_is_wrapped_as_native_pdf_with_original_digest():
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGB", (100, 100), "white").save(buffer, format="PNG")
    data = buffer.getvalue()
    doc = {"data": data, "sha256": hashlib.sha256(data).hexdigest(), "media_type": "image/png", "source_url": "official"}
    reader = Reader(extraction(doc))
    result = await run(reader, documents=[doc])
    assert result["status"] == "validated"
    assert reader.calls[0]["attachments"][0].data.startswith(b"%PDF")
    assert result["rows"][0]["document_sha256"] == doc["sha256"]


def routes(*models):
    return [{"model_key": model, "available_for_routing": True, "capabilities": ["native_file"]} for model in models]


async def test_insufficient_distinct_native_models_fail_before_enqueue(monkeypatch):
    monkeypatch.setattr("src.ai.model_center.get_runtime_routes", AsyncMock(return_value=[
        *routes("a:same", "b:same"),
        {"model_key": "p:text", "available_for_routing": True, "capabilities": ["text"]},
    ]))
    factory = Mock()
    monkeypatch.setattr(review, "ModelCenterCompleter", factory)
    result = await review.review_image_report(documents=[document()], province_code="CN-BJ", report_month="2026-09")
    assert result["status"] == "needs_review"
    assert "no durable work enqueued" in result["audit"]["errors"][0]
    assert len(result["audit"]["readings"]) == 3
    assert all(slot["status"] == "absent" for slot in result["audit"]["readings"])
    factory.assert_not_called()


async def test_production_experts_use_fresh_authors_with_cumulative_strict_avoidance(monkeypatch):
    route_reader = AsyncMock(return_value=routes("p:a", "p:b", "p:c"))
    monkeypatch.setattr("src.ai.model_center.get_runtime_routes", route_reader)
    readers = [Reader([extraction()], models=[name]) for name in ("p:a", "p:b", "p:c")]
    factory = Mock(side_effect=readers)
    monkeypatch.setattr(review, "ModelCenterCompleter", factory)
    result = await review.review_image_report(documents=[document()], province_code="CN-BJ", report_month="2026-09")
    assert result["status"] == "validated"
    assert len(result["audit"]["readings"]) == factory.call_count == 3
    assert route_reader.await_count == 4
    expected_avoid = [(), ("p:a", "a"), ("p:a", "a", "p:b", "b")]
    for index, call in enumerate(factory.call_args_list):
        assert call.kwargs == {"line": f"cn_province_image_expert_{index + 1}", "max_tokens": 24000,
            "allow_single_model": False, "capability": "native_file", "require_distinct_model": True,
            "avoid_models": expected_avoid[index]}
        assert len(readers[index].calls) == 1 and readers[index].calls[0]["role"] == "author"
    assert len({reader.calls[0]["prompt"] for reader in readers}) == 1
    assert result["audit"]["extractor_config"]["max_tokens"] == 24000
    assert result["audit"]["extractor_config"]["temperature"] == 0.1


async def test_two_available_models_fill_two_seats_and_third_remains_explicitly_absent(monkeypatch):
    monkeypatch.setattr("src.ai.model_center.get_runtime_routes", AsyncMock(return_value=routes("p:a", "p:b")))
    factory = Mock(side_effect=[Reader([extraction()], models=[name]) for name in ("p:a", "p:b")])
    monkeypatch.setattr(review, "ModelCenterCompleter", factory)
    result = await review.review_image_report(documents=[document()], province_code="CN-BJ", report_month="2026-09")
    audit = result["audit"]
    assert result["status"] == "validated" and audit["winner_votes"] == 2
    assert audit["panel_size"] == len(audit["readings"]) == 3 and audit["quorum"] == 2
    assert audit["native_readings"] == factory.call_count == 2
    assert audit["readings"][2]["status"] == "absent"
    assert audit["readings"][2]["reason"] == "no_untried_native_model_available"


async def test_native_model_capacity_reports_quorum_and_missing_backup(monkeypatch):
    monkeypatch.setattr("src.ai.model_center.get_runtime_routes", AsyncMock(return_value=[
        *routes("provider-a:same", "provider-b:same", "provider-c:other"),
        {"model_key": "p:text-only", "available_for_routing": True, "capabilities": ["text"]},
    ]))
    capacity = await review.native_model_capacity()
    assert capacity["distinct_native_models"] == 2
    assert capacity["quorum_available"] is True
    assert capacity["full_panel_available"] is False
    assert capacity["missing_for_full_panel"] == 1
    assert capacity["native_model_identities"] == ["other", "same"]


async def test_pending_audit_names_action_without_weakening_vote():
    result = await run(Reader([extraction(), "not JSON", RuntimeError("route lost")]))
    assert result["status"] == "needs_review" and result["rows"] == []
    assert result["audit"]["next_action"] == "inspect_expert_abstentions_then_retry_or_review_originals_manually"
    assert result["audit"]["quorum"] == 2


async def test_route_loss_before_later_seat_does_not_enqueue_unserviceable_work(monkeypatch):
    monkeypatch.setattr("src.ai.model_center.get_runtime_routes", AsyncMock(side_effect=[
        routes("p:a", "p:b", "p:c"), routes("p:a", "p:b", "p:c"), routes("p:a"), routes("p:a")]))
    factory = Mock(return_value=Reader([extraction()], models=["p:a"]))
    monkeypatch.setattr(review, "ModelCenterCompleter", factory)
    result = await review.review_image_report(documents=[document()], province_code="CN-BJ", report_month="2026-09")
    assert result["status"] == "needs_review" and factory.call_count == 1
    assert [slot["status"] for slot in result["audit"]["readings"]] == ["voted", "absent", "absent"]


async def test_all_runtime_failures_preserve_fixed_panel_and_failure_evidence():
    reader = Reader([RuntimeError("provider unavailable")] * 3)
    result = await run(reader)
    assert result["status"] == "needs_review" and result["rows"] == []
    assert len(reader.calls) == len(result["audit"]["readings"]) == 3
    assert all(slot["status"] == "abstained" and "provider unavailable" in slot["reason"] for slot in result["audit"]["readings"])
