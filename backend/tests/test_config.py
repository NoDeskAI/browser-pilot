import pytest

from app import config


def test_ee_env_keeps_project_root_importable(monkeypatch):
    root = str(config.PROJECT_ROOT)
    monkeypatch.setattr(config, "_env", lambda key, default: "ee" if key == "EDITION" else default)
    monkeypatch.setattr(config.sys, "path", [entry for entry in config.sys.path if entry != root])

    assert config._detect_edition() == "ee"
    assert config.sys.path[0] == root


def test_require_database_url_returns_configured_value(monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://localhost:5432/app")

    assert config.require_database_url() == "postgresql://localhost:5432/app"


def test_require_database_url_reports_missing_value(monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", "")

    with pytest.raises(RuntimeError) as exc:
        config.require_database_url()

    message = str(exc.value)
    assert "DATABASE_URL is not set" in message
    assert "Copy .env.example to .env" in message
