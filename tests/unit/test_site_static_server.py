from scripts.serve_site import accepts_gzip, cache_control_for


def test_static_server_cache_policy_distinguishes_html_assets_and_data() -> None:
    assert cache_control_for("/countries/jp/") == "public, max-age=0, must-revalidate"
    assert cache_control_for("/_astro/app.hash.js") == "public, max-age=31536000, immutable"
    assert cache_control_for("/site-data/countries/jp.json") == (
        "public, max-age=300, stale-while-revalidate=86400"
    )


def test_static_server_only_uses_gzip_when_the_client_accepts_it() -> None:
    assert accepts_gzip("br, gzip;q=0.8")
    assert not accepts_gzip("br, deflate")
