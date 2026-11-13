from types import SimpleNamespace

from scripts import check_theme_publication_readiness as readiness


def config(**overrides):
    values = {
        "theme_publish_enabled": False,
        "theme_compat_v1_ready": False,
        "theme_r2_bucket": "",
        "theme_public_base_url": "",
        "theme_stylesheet_url": "",
        "r2_papers_bucket": "papers",
        "r2_reports_bucket": "reports",
    }
    values.update(overrides)
    return SimpleNamespace(data_release=SimpleNamespace(**values))


def test_missing_theme_infrastructure_fails_closed_without_network(monkeypatch):
    monkeypatch.setattr(readiness, "get_config", lambda: config())

    def unexpected_probe(*args, **kwargs):
        raise AssertionError("network probe must not run for missing configuration")

    monkeypatch.setattr(readiness, "urlopen", unexpected_probe)
    result = readiness.check()

    assert result["ready"] is False
    assert "theme_public_bucket_unconfigured" in result["reasons"]
    assert "theme_public_destination_unconfigured" in result["reasons"]
    assert "theme_compatible_templates_not_verified" in result["reasons"]
    assert "theme_publication_disabled" in result["reasons"]
    assert result["public_probe"] is None


def test_public_css_requires_https_text_css_and_no_redirect(monkeypatch):
    monkeypatch.setattr(
        readiness,
        "get_config",
        lambda: config(
            theme_publish_enabled=True,
            theme_compat_v1_ready=True,
            theme_r2_bucket="theme-assets",
            theme_public_base_url="https://static.example",
            theme_stylesheet_url="https://static.example/theme/compat/v1/theme.css",
        ),
    )

    class Response:
        headers = {"Content-Type": "text/html; charset=utf-8"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, limit):
            return b"not css"

        def geturl(self):
            return "https://static.example/theme/compat/v1/theme.css"

    monkeypatch.setattr(readiness, "urlopen", lambda *args, **kwargs: Response())
    result = readiness.check()

    assert result["ready"] is False
    assert result["public_probe"]["error_code"] == "theme_public_content_type_invalid"
    assert result["reasons"] == ["theme_public_content_type_invalid"]


def test_fully_verified_theme_is_ready_after_css_probe(monkeypatch):
    monkeypatch.setattr(
        readiness,
        "get_config",
        lambda: config(
            theme_publish_enabled=True,
            theme_compat_v1_ready=True,
            theme_r2_bucket="theme-assets",
            theme_public_base_url="https://static.example",
            theme_stylesheet_url="https://static.example/theme/compat/v1/theme.css",
        ),
    )

    class Response:
        headers = {"Content-Type": "text/css; charset=utf-8"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, limit):
            return b":root { --gids-theme: ready; }"

        def geturl(self):
            return "https://static.example/theme/compat/v1/theme.css"

    monkeypatch.setattr(readiness, "urlopen", lambda *args, **kwargs: Response())
    result = readiness.check()

    assert result["ready"] is True
    assert result["reasons"] == []
    assert result["public_probe"]["available"] is True
