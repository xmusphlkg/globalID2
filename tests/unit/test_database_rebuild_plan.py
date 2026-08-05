from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from src.services.database_rebuild_plan import (
    REBUILD_OPTION_KEYS,
    build_rebuild_plan,
)


@pytest.mark.parametrize(
    ("mode", "expected_options", "expected_deletions"),
    [
        (
            "full",
            {
                "clear_data": True,
                "import_standard": True,
                "import_mappings": True,
                "sync_diseases": True,
                "import_history": True,
            },
            (
                "disease_records",
                "disease_mappings",
                "disease_learning_suggestions",
            ),
        ),
        (
            "mappings",
            {
                "clear_data": True,
                "import_standard": True,
                "import_mappings": True,
                "sync_diseases": True,
                "import_history": False,
            },
            ("disease_mappings",),
        ),
        (
            "history",
            {
                "clear_data": True,
                "import_standard": False,
                "import_mappings": False,
                "sync_diseases": False,
                "import_history": True,
            },
            ("disease_records",),
        ),
    ],
)
def test_builtin_rebuild_plans_preserve_legacy_steps_and_deletion_scope(
    mode: str,
    expected_options: dict[str, bool],
    expected_deletions: tuple[str, ...],
) -> None:
    plan = build_rebuild_plan(mode)

    assert plan.options() == expected_options
    assert plan.deletion_tables == expected_deletions


def test_mode_plan_keeps_operator_warning_scope() -> None:
    mappings = build_rebuild_plan("mappings")
    history = build_rebuild_plan("history")

    assert mappings.tables_to_clear == (
        "disease_mappings",
        "standard_diseases",
    )
    assert mappings.preserved_tables[0] == "disease_records (历史数据)"
    assert history.tables_to_clear == ("disease_records",)
    assert "disease_mappings" in history.preserved_tables


def test_custom_plan_requires_a_complete_boolean_option_set() -> None:
    options = {key: False for key in REBUILD_OPTION_KEYS}
    options["import_history"] = True

    plan = build_rebuild_plan("custom", options)

    assert plan.options() == options
    assert plan.deletion_tables == (
        "disease_records",
        "disease_mappings",
        "disease_learning_suggestions",
    )


@pytest.mark.parametrize(
    ("options", "error", "match"),
    [
        (None, ValueError, "requires explicit step options"),
        ({"clear_data": True}, ValueError, "missing:"),
        (
            {**{key: True for key in REBUILD_OPTION_KEYS}, "unexpected": True},
            ValueError,
            "unknown: unexpected",
        ),
        (
            {**{key: True for key in REBUILD_OPTION_KEYS}, "clear_data": 1},
            TypeError,
            "must be booleans: clear_data",
        ),
    ],
)
def test_custom_plan_rejects_ambiguous_configuration(
    options: dict[str, bool] | None,
    error: type[Exception],
    match: str,
) -> None:
    with pytest.raises(error, match=match):
        build_rebuild_plan("custom", options)


def test_plan_rejects_unknown_modes_and_builtin_overrides() -> None:
    with pytest.raises(ValueError, match="Unsupported rebuild mode"):
        build_rebuild_plan("everything")

    with pytest.raises(ValueError, match="only be used with custom mode"):
        build_rebuild_plan("full", {key: True for key in REBUILD_OPTION_KEYS})


def test_plan_is_immutable_and_options_are_returned_as_a_copy() -> None:
    plan = build_rebuild_plan("full")
    options = plan.options()
    options["clear_data"] = False

    assert plan.clear_data is True
    with pytest.raises(FrozenInstanceError):
        plan.clear_data = False  # type: ignore[misc]
