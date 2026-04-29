import pytest

from app import config


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
