import asyncio
from pathlib import Path

import yaml

from app import runtime_control


def test_validate_runtime_command_rejects_non_docker_command():
    try:
        runtime_control.validate_runtime_command("curl http://example.com")
    except runtime_control.RuntimeCommandRejected as exc:
        assert "docker commands" in str(exc)
    else:
        raise AssertionError("non-docker command should be rejected")


def test_validate_runtime_command_allows_known_docker_command():
    runtime_control.validate_runtime_command("docker ps -a")


def test_validate_runtime_command_rejects_host_shell_chain():
    try:
        runtime_control.validate_runtime_command("docker ps ; curl http://example.com")
    except runtime_control.RuntimeCommandRejected as exc:
        assert "control operators" in str(exc)
    else:
        raise AssertionError("host shell chain should be rejected")


def test_validate_runtime_command_rejects_host_shell_line_break():
    try:
        runtime_control.validate_runtime_command("docker ps\ncurl http://example.com")
    except runtime_control.RuntimeCommandRejected as exc:
        assert "line breaks" in str(exc)
    else:
        raise AssertionError("host shell line break should be rejected")


def test_validate_runtime_command_rejects_host_command_substitution():
    try:
        runtime_control.validate_runtime_command("docker ps $(curl http://example.com)")
    except runtime_control.RuntimeCommandRejected as exc:
        assert "command substitution" in str(exc)
    else:
        raise AssertionError("host command substitution should be rejected")


def test_validate_runtime_command_allows_container_shell_script_argument():
    runtime_control.validate_runtime_command(
        "docker exec bp-session sh -c 'ip route | grep -E \"dev tun[0-9]+\" >/dev/null && echo $(date)'"
    )


def test_remote_runtime_requires_control_token(monkeypatch):
    monkeypatch.setattr(runtime_control, "BROWSER_RUNTIME_CONTROL_URL", "http://runtime-worker:8001")
    monkeypatch.setattr(runtime_control, "BROWSER_RUNTIME_CONTROL_TOKEN", "")
    monkeypatch.setattr(runtime_control, "EE_SAAS_MODE", False)

    stdout, stderr, rc = asyncio.run(runtime_control.run_runtime_command("docker ps", timeout=1))

    assert stdout == ""
    assert rc == -1
    assert "BROWSER_RUNTIME_CONTROL_TOKEN" in stderr


def test_saas_mode_disables_runtime_shell_commands(monkeypatch):
    monkeypatch.setattr(runtime_control, "EE_SAAS_MODE", True)
    monkeypatch.setattr(runtime_control, "BROWSER_RUNTIME_CONTROL_URL", "")

    stdout, stderr, rc = asyncio.run(runtime_control.run_runtime_command("docker ps", timeout=1))

    assert stdout == ""
    assert rc == -1
    assert "disabled in EE SaaS mode" in stderr


def test_docker_compose_keeps_docker_socket_on_runtime_worker_only():
    root = Path(__file__).resolve().parents[2]
    compose = yaml.safe_load((root / "docker-compose.yml").read_text())

    backend_volumes = compose["services"]["backend"].get("volumes", [])
    worker_volumes = compose["services"]["runtime-worker"].get("volumes", [])

    assert all("/var/run/docker.sock" not in str(volume) for volume in backend_volumes)
    assert any("/var/run/docker.sock:/var/run/docker.sock" == str(volume) for volume in worker_volumes)
