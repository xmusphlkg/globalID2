"""Cloudflare inventory is GET-only, permission-aware and strips private config."""
from scripts.audit_publication_cloudflare import API, ReadOnlyCloudflare, audit


class Response:
    def __init__(self, result=None, *, status=200, info=None):
        self.status_code = status
        self.payload = {"success": status == 200, "result": result, "result_info": info or {}}

    def json(self):
        return self.payload


class Session:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses(url, kwargs)


def test_read_uses_get_without_redirects_and_does_not_export_permission_body():
    session = Session(lambda url, kwargs: Response({"token": "secret"}, status=403))
    client = ReadOnlyCloudflare("secret", session=session)
    result = client.read("/accounts/account/workers/domains")
    assert result == {"status": "unknown", "reason": "permission_denied", "http_status": 403}
    assert session.calls[0][0].startswith(API)
    assert session.calls[0][1]["allow_redirects"] is False
    assert session.calls[0][1]["headers"]["Authorization"] == "Bearer secret"
    assert "secret" not in str(result)


def test_missing_token_is_unknown_and_does_not_send_any_request():
    session = Session(lambda *_: (_ for _ in ()).throw(AssertionError("No request expected")))
    client = ReadOnlyCloudflare("", session=session)
    result = audit(client, account_id="", project_name="gids", zone_name="example.test")
    assert result["zones"]["status"] == "unknown"
    assert result["worker_domains"]["reason"] == "account_unavailable"
    assert result["worker_routes"][0]["status"] == "unknown"
    assert result["worker_bypass_verified"] is False
    assert session.calls == []


def test_inventory_keeps_public_routing_and_cors_without_project_secrets():
    def responses(url, kwargs):
        path = url.removeprefix(API)
        if path == "/zones":
            return Response([{"id": "zone-secret-id", "name": "example.test", "status": "active", "account": {"id": "account-id"}}])
        if path.endswith("/rulesets/phases/http_request_cache_settings/entrypoint"):
            return Response({"kind": "zone", "phase": "http_request_cache_settings", "rules": []})
        if path.endswith("/workers/routes"):
            return Response([{"id": "route-id", "pattern": "example.test/site-data/*", "script": "data"}])
        if path.endswith("/workers/domains"):
            return Response([{"id": "domain-id", "hostname": "papers.example.test", "service": "papers"}])
        if path.endswith("/pages/projects/gids"):
            return Response({"name": "gids", "domains": ["example.test"], "deployment_configs": {"production": {"env_vars": {"PRIVATE": "never-export"}}}})
        if path.endswith("/pages/projects/gids/domains"):
            return Response([{"name": "example.test", "status": "active"}])
        if path.endswith("/r2/buckets"):
            return Response({"buckets": [{"name": "public-data", "location": "APAC"}]})
        if path.endswith("/domains/custom"):
            return Response({"domains": [{"domain": "static.example.test", "enabled": True, "zoneId": "zone-secret-id"}]})
        if path.endswith("/cors"):
            return Response({"rules": [{"allowed": {"origins": ["*"], "methods": ["GET", "HEAD"]}, "maxAgeSeconds": 3600}], "internal": "never-export"})
        raise AssertionError(path)
    session = Session(responses)
    result = audit(ReadOnlyCloudflare("api-secret", session=session), account_id="account-id", project_name="gids", zone_name="example.test")
    assert result["worker_routes"][0]["data"] == [{"pattern": "example.test/site-data/*", "script": "data"}]
    assert result["pages_project"]["data"] == {"name": "gids", "domains": ["example.test"]}
    bucket = result["r2_buckets"]["data"][0]
    assert bucket["custom_domains"]["data"] == [{"domain": "static.example.test", "enabled": True}]
    assert bucket["cors"]["rules"][0]["allowed"]["methods"] == ["GET", "HEAD"]
    assert all(secret not in str(result) for secret in ("api-secret", "account-id", "zone-secret-id", "never-export"))
    assert not any("/objects" in url for url, _ in session.calls)


def test_listing_follows_pages_and_keeps_permission_failure_unknown():
    def responses(url, kwargs):
        if kwargs["params"].get("page") == 2:
            return Response(status=403)
        return Response([{"id": "one"}], info={"page": 1, "total_pages": 2})
    result = ReadOnlyCloudflare("token", session=Session(responses)).listing("/zones")
    assert result["status"] == "unknown"
    assert result["data"] == [{"id": "one"}]
    assert result["reason"] == "permission_denied"


def test_r2_listing_follows_cursor_and_rejects_repeated_cursor():
    session = Session(lambda *_: Response({"buckets": [{"name": "public-data"}]}, info={"cursor": "same"}))
    result = ReadOnlyCloudflare("token", session=session).listing("/accounts/a/r2/buckets", collection="buckets")
    assert result["status"] == "unknown"
    assert result["reason"] == "invalid_pagination"
    assert len(session.calls) == 2


def test_404_and_invalid_payload_are_unknown_not_empty_healthy_data():
    client = ReadOnlyCloudflare("token", session=Session(lambda *_: Response(status=404)))
    assert client.read("/accounts/a/r2/buckets/b/cors")["status"] == "unknown"
    client = ReadOnlyCloudflare("token", session=Session(lambda *_: Response({"unexpected": True})))
    assert client.listing("/zones")["reason"] == "invalid_api_response"
