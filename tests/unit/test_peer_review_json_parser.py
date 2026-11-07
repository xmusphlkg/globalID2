"""parse_json_object must survive prose and extra braces around the payload."""

from __future__ import annotations

import pytest

from src.ai.peer_review import AIOutputError, parse_json_object


def test_plain_object():
    assert parse_json_object('{"a": 1}') == {"a": 1}


def test_fenced_object():
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}


def test_trailing_prose_with_brace_does_not_corrupt_payload():
    assert parse_json_object('{"a": 1}\nNote: see {appendix}.') == {"a": 1}


def test_leading_prose_with_brace_is_skipped():
    assert parse_json_object('Here {is} the result: {"a": {"b": 2}}') == {"a": {"b": 2}}


def test_second_object_after_payload_is_ignored():
    assert parse_json_object('{"a": 1} {"b": 2}') == {"a": 1}


def test_braces_inside_strings_are_kept():
    assert parse_json_object('{"a": "x } y"}') == {"a": "x } y"}


def test_no_object_raises_ai_output_error():
    with pytest.raises(AIOutputError):
        parse_json_object("no json here")


def test_invalid_json_raises_ai_output_error_and_value_error():
    with pytest.raises(ValueError):
        parse_json_object('{"a": }')
