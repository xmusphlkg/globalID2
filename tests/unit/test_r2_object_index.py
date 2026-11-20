from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import sqlite3
import threading

import pytest

from src.services.data_release.r2_object_index import (
    R2ObjectIndex,
    R2ObjectIndexError,
    R2ReservationConflict,
    R2StorageBudgetExceeded,
    metadata_matches,
)


def make_index(tmp_path, *, bucket="papers", endpoint="https://account.r2.example", limit=100):
    return R2ObjectIndex(endpoint, bucket, path=tmp_path / "objects.sqlite3", max_total_bytes=limit)


def record(index, key="object", size=10, etag="abcd"):
    index.record(key, size=size, etag=etag, content_type="application/json", content_encoding="gzip",
                 cache_control="public, immutable")


def matches(index, key="object", size=10, etag="abcd", **overrides):
    fields = dict(size=size, etag=etag, content_type="application/json", content_encoding="gzip",
                  cache_control="public, immutable")
    fields.update(overrides)
    return index.matches(key, **fields)


def test_confirmed_objects_survive_restart_and_are_target_isolated(tmp_path):
    index = make_index(tmp_path)
    index.replace_inventory({})
    token = index.reserve("object", 10)
    index.commit(token, etag='"abcd"', content_type="application/json", content_encoding="gzip",
                 cache_control="public, immutable")
    index.close()
    restarted = make_index(tmp_path)
    assert matches(restarted)
    assert restarted.total_bytes() == 10
    assert restarted.inventory_is_fresh()
    assert not matches(restarted, size=11)
    assert not matches(restarted, etag="different")
    assert not matches(restarted, content_encoding=None)
    assert not matches(restarted, cache_control="no-store")
    assert not matches(make_index(tmp_path, bucket="reports"))
    assert not matches(make_index(tmp_path, endpoint="https://other-account.r2.example"))
    assert matches(make_index(tmp_path, endpoint="https://account.r2.example/"))


def test_inventory_preserves_only_verified_unchanged_metadata_and_removes_deleted_keys(tmp_path):
    index = make_index(tmp_path)
    record(index)
    record(index, "changed", etag="old")
    record(index, "deleted", size=20)
    index.replace_inventory({"object": {"bytes": 10, "etag": "abcd"},
                             "changed": {"bytes": 15, "etag": "new"},
                             "new": {"bytes": 8, "etag": "efgh"}})
    assert matches(index)
    assert not index.get("changed")["metadata_known"]
    assert not index.get("new")["metadata_known"]
    assert index.get("deleted") is None
    assert index.total_bytes() == 33
    assert set(index.snapshot()) == {"object", "changed", "new"}
    index.delete(["changed", "changed", "absent"])
    assert index.total_bytes() == 18


def test_no_uploads_before_complete_inventory_and_uncertain_metadata_requires_refresh(tmp_path):
    index = make_index(tmp_path)
    record(index)
    with pytest.raises(R2ObjectIndexError, match="complete"):
        index.reserve("new", 1)
    with pytest.raises(R2ObjectIndexError, match="complete"):
        index.preflight({"new": 1})
    index.replace_inventory({"object": {"bytes": 10, "etag": "abcd"}})
    index.invalidate("object")
    assert not matches(index)
    assert not index.inventory_is_fresh()
    assert index.total_bytes() == 0
    with pytest.raises(R2ObjectIndexError, match="complete"):
        index.reserve("new", 1)


def test_definite_failed_put_does_not_record_existence_or_consume_capacity(tmp_path):
    index = make_index(tmp_path)
    index.replace_inventory({})
    token = index.reserve("object", 99)
    assert index.get("object") is None
    assert index.pending_bytes() == 99
    index.release(token)
    assert index.pending_bytes() == 0
    assert index.total_bytes() == 0
    assert not matches(index, size=99)


def test_invalid_success_receipt_keeps_reservation_and_previous_object_atomically(tmp_path):
    index = make_index(tmp_path)
    index.replace_inventory({"object": {"bytes": 10, "etag": "old"}})
    token = index.reserve("object", 40)
    with pytest.raises(ValueError):
        index.commit(token, etag="")
    assert index.get("object")["etag"] == "old"
    assert index.total_bytes() == 10
    assert index.pending_bytes() == 30
    index.commit(token, etag="confirmed")
    assert index.get("object")["etag"] == "confirmed"
    assert index.total_bytes() == 40


def test_uncertain_writes_remain_reserved_across_restart_until_explicit_reconciliation(tmp_path):
    index = make_index(tmp_path)
    index.replace_inventory({})
    token = index.reserve("uncertain", 90)
    index.close()
    restarted = make_index(tmp_path)
    assert restarted.pending_bytes() == 90
    with pytest.raises(R2StorageBudgetExceeded):
        restarted.reserve("new", 11)
    with pytest.raises(R2ReservationConflict):
        restarted.replace_inventory({})
    with pytest.raises(R2ReservationConflict):
        restarted.delete("uncertain")
    restarted.replace_inventory({"uncertain": {"bytes": 90, "etag": "resolved"}}, recover_reservations=True)
    assert restarted.pending_bytes() == 0
    assert restarted.total_bytes() == 90
    with pytest.raises(R2ReservationConflict):
        restarted.commit(token, etag="resolved")


def test_two_independent_connections_cannot_overbook_budget(tmp_path):
    first, second = make_index(tmp_path), make_index(tmp_path)
    first.replace_inventory({"existing": {"bytes": 60, "etag": "old"}})
    barrier = threading.Barrier(2)

    def upload(index, key):
        barrier.wait()
        try:
            return index.reserve(key, 30)
        except R2StorageBudgetExceeded:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda args: upload(*args), [(first, "a"), (second, "b")]))
    assert sum(token is not None for token in results) == 1
    assert first.total_bytes() == 60
    assert first.pending_bytes() == 30


def test_same_key_cannot_be_reserved_twice_or_modified_while_pending(tmp_path):
    first, second = make_index(tmp_path), make_index(tmp_path)
    first.replace_inventory({})
    first.reserve("object", 10)
    with pytest.raises(R2ReservationConflict):
        second.reserve("object", 10)
    with pytest.raises(R2ReservationConflict):
        record(second)
    assert first.total_bytes() == 0


def test_overwrite_reserves_only_growth_and_shrink_frees_bytes_after_confirmation(tmp_path):
    index = make_index(tmp_path)
    index.replace_inventory({"object": {"bytes": 80, "etag": "old"}})
    token = index.reserve("object", 90)
    assert index.pending_bytes() == 10
    index.commit(token, etag="new")
    assert index.total_bytes() == 90
    assert index.pending_bytes() == 0
    token = index.reserve("object", 30)
    assert index.total_bytes() == 90
    index.commit(token, etag="small")
    assert index.total_bytes() == 30
    index.delete("object")
    assert index.total_bytes() == 0


def test_preflight_rejects_complete_candidate_without_partially_reserving(tmp_path):
    index = make_index(tmp_path)
    index.replace_inventory({"existing": {"bytes": 70, "etag": "old"}})
    with pytest.raises(R2StorageBudgetExceeded):
        index.preflight({"existing": 70, "a": 20, "b": 20})
    assert index.pending_bytes() == 0
    check = index.preflight({"existing": 70, "a": 20})
    assert check["projected_bytes"] == 90
    index.reserve("in-flight", 15)
    with pytest.raises(R2StorageBudgetExceeded):
        index.preflight({"a": 20})


def test_existing_over_budget_bucket_blocks_even_zero_growth_put(tmp_path):
    index = make_index(tmp_path)
    index.replace_inventory({"object": {"bytes": 101, "etag": "old"}})
    with pytest.raises(R2StorageBudgetExceeded):
        index.preflight({})
    with pytest.raises(R2StorageBudgetExceeded):
        index.reserve("object", 1)


def test_bad_inventory_is_atomic_and_cannot_make_bucket_appear_smaller(tmp_path):
    index = make_index(tmp_path)
    index.replace_inventory({"object": {"bytes": 50, "etag": "old"}})
    with pytest.raises(ValueError):
        index.replace_inventory({"new": {"bytes": -1, "etag": "bad"}})
    assert index.total_bytes() == 50
    assert index.get("object") is not None


def test_corrupt_database_fails_closed_and_does_not_get_replaced(tmp_path):
    path = tmp_path / "objects.sqlite3"
    path.write_bytes(b"corrupt data")
    with pytest.raises(sqlite3.DatabaseError):
        make_index(tmp_path)
    assert path.read_bytes() == b"corrupt data"


def test_default_path_can_be_set_without_sharing_other_bucket_state(tmp_path, monkeypatch):
    path = tmp_path / "nested" / "index.sqlite3"
    monkeypatch.setenv("R2_OBJECT_INDEX_PATH", str(path))
    index = R2ObjectIndex("https://account.r2.example", "papers")
    assert index.path == path
    assert path.exists()


def test_decoded_content_proof_survives_wire_recipe_change_but_not_remote_changes(tmp_path):
    index = make_index(tmp_path)
    digest = hashlib.sha256(b"same decoded content").hexdigest()
    index.replace_inventory({})
    token = index.reserve("immutable", 10)
    index.commit(token, etag="compressed-wire", content_type="application/json", content_encoding="gzip",
                 cache_control="immutable", content_sha256=digest)
    assert index.matches_content("immutable", digest, "application/json", "immutable")
    assert not index.matches("immutable", size=20, etag="different-compression", content_type="application/json",
                             content_encoding="gzip", cache_control="immutable")
    index.close()
    index = make_index(tmp_path)
    index.replace_inventory({"immutable": {"bytes": 10, "etag": "compressed-wire"}})
    assert index.matches_content("immutable", digest, "application/json", "immutable")
    index.record("immutable", size=10, etag="compressed-wire", content_type="application/json",
                 content_encoding="gzip", cache_control="immutable")
    assert index.matches_content("immutable", digest, "application/json", "immutable")
    assert not index.matches_content("immutable", digest, "text/plain", "immutable")
    index.replace_inventory({"immutable": {"bytes": 10, "etag": "changed"}})
    assert index.get("immutable")["content_sha256"] is None
    assert not index.matches_content("immutable", digest, "application/json", "immutable")


def test_head_can_resolve_unknown_put_without_unreserved_gap(tmp_path):
    index = make_index(tmp_path)
    index.replace_inventory({"pointer": {"bytes": 10, "etag": "old"}})
    token = index.reserve("pointer", 25)
    index.resolve(token, {"size": 25, "etag": "written", "content_type": "application/json"})
    assert index.total_bytes() == 25
    assert index.pending_bytes() == 0
    assert index.get("pointer")["etag"] == "written"
    token = index.reserve("pointer", 30)
    index.resolve(token, None)
    assert index.total_bytes() == 0
    assert index.pending_bytes() == 0
    assert index.get("pointer") is None


def test_invalid_head_resolution_keeps_capacity_reserved(tmp_path):
    index = make_index(tmp_path)
    index.replace_inventory({})
    token = index.reserve("object", 30)
    with pytest.raises(ValueError):
        index.resolve(token, {"size": -1, "etag": "invalid"})
    assert index.pending_bytes() == 30
    assert index.get("object") is None


def test_reservations_are_read_only_and_filterable_for_fenced_reconciliation(tmp_path):
    index = make_index(tmp_path)
    index.replace_inventory({})
    first = index.reserve("first", 10)
    second = index.reserve("second", 20)
    rows = index.reservations()
    assert [row["token"] for row in rows] == [first, second]
    assert rows[0]["key"] == "first" and rows[0]["growth"] == 10
    assert index.reservations(["second"])[0]["token"] == second
    assert index.reservations([]) == []


def test_schema_one_index_is_migrated_without_losing_existing_receipts(tmp_path):
    path = tmp_path / "objects.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE r2_objects (
            target TEXT NOT NULL,key TEXT NOT NULL,bytes INTEGER NOT NULL,etag TEXT NOT NULL,
            content_type TEXT,content_encoding TEXT,cache_control TEXT,metadata_known INTEGER NOT NULL,
            confirmed_at REAL NOT NULL,PRIMARY KEY(target,key))""")
        db.execute("PRAGMA user_version=1")
    index = make_index(tmp_path)
    digest = "a" * 64
    index.record("object", size=10, etag="wire", content_type="application/json", content_sha256=digest)
    assert index.matches_content("object", digest, "application/json", None)


def test_content_type_normalization_preserves_case_sensitive_boundary_values():
    first = {"content_type": 'Multipart/Mixed ; boundary="Aa;Bb,Cc" ; charset="UTF-8"'}
    same = {"content_type": 'multipart/mixed;charset=utf-8;BOUNDARY="Aa;Bb,Cc"'}
    different = {"content_type": 'multipart/mixed;charset=utf-8;boundary="aa;bb,cc"'}
    assert metadata_matches(first, same)
    assert not metadata_matches(first, different)


def test_cache_directives_split_only_outside_quotes_and_preserve_extension_values():
    first = {"cache_control": 'public, ext="Aa,Bb", max-age="100"'}
    same = {"cache_control": 'MAX-AGE=100,EXT="Aa,Bb",PUBLIC'}
    different = {"cache_control": 'max-age=100,ext="aa,bb",public'}
    assert metadata_matches(first, same)
    assert not metadata_matches(first, different)
    assert not metadata_matches(first, {"cache_control": 'max-age=100,ext="Aa,Bb",private'})


def test_content_encoding_normalization_preserves_transform_order_and_can_be_excluded_for_decoded_proofs():
    assert metadata_matches({"content_encoding": " GZip, BR "}, {"content_encoding": "gzip,br"})
    assert not metadata_matches({"content_encoding": "gzip,br"}, {"content_encoding": "br,gzip"})
    assert metadata_matches({"content_encoding": "gzip", "content_type": "Text/HTML"},
                            {"content_type": "text/html"}, compare_encoding=False)
    assert not metadata_matches({"content_encoding": "gzip", "content_type": "text/html", "cache_control": "no-store"},
                                {"content_type": "text/html", "cache_control": "immutable"}, compare_encoding=False)
