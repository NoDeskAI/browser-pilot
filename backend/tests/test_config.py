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


def _set_valid_production_public_config(monkeypatch):
    monkeypatch.setattr(config, "APP_ENV", "production")
    monkeypatch.setattr(config, "APP_PUBLIC_ORIGINS", ["https://app.example.com"])
    monkeypatch.setattr(config, "BROWSER_RUNTIME_ACCESS_MODE", "private")
    monkeypatch.setattr(config, "VIEWER_TICKET_TTL_SECONDS", 60)
    monkeypatch.setattr(config, "FILE_DOWNLOAD_URL_TTL_SECONDS", 300)
    monkeypatch.setattr(config, "BROWSER_VNC_PASSWORD_SECRET", "shared-vnc-secret")


def test_production_kubernetes_runtime_requires_vnc_secret_but_not_docker_control(monkeypatch):
    _set_valid_production_public_config(monkeypatch)
    monkeypatch.setattr(config, "BROWSER_RUNTIME_PROVIDER", "kubernetes")
    monkeypatch.setattr(config, "BROWSER_RUNTIME_CONTROL_URL", "")
    monkeypatch.setattr(config, "BROWSER_RUNTIME_CONTROL_TOKEN", "")

    config.validate_public_runtime_config()

    monkeypatch.setattr(config, "BROWSER_VNC_PASSWORD_SECRET", "")
    with pytest.raises(RuntimeError, match="BROWSER_VNC_PASSWORD_SECRET must be set"):
        config.validate_public_runtime_config()


def test_production_docker_runtime_still_requires_runtime_control(monkeypatch):
    _set_valid_production_public_config(monkeypatch)
    monkeypatch.setattr(config, "BROWSER_RUNTIME_PROVIDER", "docker")
    monkeypatch.setattr(config, "BROWSER_RUNTIME_CONTROL_URL", "")
    monkeypatch.setattr(config, "BROWSER_RUNTIME_CONTROL_TOKEN", "")

    with pytest.raises(RuntimeError, match="BROWSER_RUNTIME_CONTROL_URL must be set"):
        config.validate_public_runtime_config()

    monkeypatch.setattr(config, "BROWSER_RUNTIME_CONTROL_URL", "http://runtime-worker:8001")
    with pytest.raises(RuntimeError, match="BROWSER_RUNTIME_CONTROL_TOKEN must be set"):
        config.validate_public_runtime_config()
