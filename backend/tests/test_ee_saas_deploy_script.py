from __future__ import annotations

import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_SCRIPT = REPO_ROOT / "deploy" / "ee-saas" / "deploy.sh"
VALID_DIGEST = "sha256:" + "a" * 64


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _fake_tool_env(tmp_path: Path, *, rendered_digest: str = VALID_DIGEST) -> dict[str, str]:
    _write_executable(
        tmp_path / "curl",
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "$*" == *"/api/platform/deploy/runtime-values"* ]]; then
  printf '{"runtime":{"approvedImages":[],"tenants":[]}}'
  exit 0
fi
if [[ "$*" == *"/api/platform/audit-events"* ]]; then
  exit 0
fi
exit 7
""",
    )
    _write_executable(
        tmp_path / "helm",
        f"""#!/usr/bin/env bash
set -euo pipefail
case "${{1:-}}" in
  lint)
    exit 0
    ;;
  template)
    printf 'metadata:\\n  labels:\\n    browser-pilot/managed-runtime-namespace: "true"\\nspec:\\n  containers:\\n    - image: ghcr.io/nodeskai/browser-pilot-ee@{rendered_digest}\\n'
    exit 0
    ;;
  upgrade)
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
""",
    )
    _write_executable(
        tmp_path / "kubectl",
        """#!/usr/bin/env bash
set -euo pipefail
args="$*"
if [[ "${args}" == *"get deploy -l"* ]]; then
  printf 'browser-pilot-ee-saas-browser-pilot-backend'
  exit 0
fi
if [[ "${args}" == *"get deploy browser-pilot-ee-saas-browser-pilot-backend"* ]]; then
  printf 'EDITION=ee\\nEE_SAAS_MODE=true\\nBROWSER_RUNTIME_PROVIDER=kubernetes\\n'
  exit 0
fi
if [[ "${args}" == *"get namespace -l browser-pilot/managed-runtime-namespace=true"* ]]; then
  printf 'bp-tenant-a\\n'
  exit 0
fi
if [[ "${args}" == *"get serviceaccount -l browser-pilot/runtime-session-service-account=true"* ]]; then
  printf 'browser-pilot-session'
  exit 0
fi
if [[ "${args}" == *"get serviceaccount browser-pilot-session"* ]]; then
  printf 'false'
  exit 0
fi
if [[ "${args}" == *"apply --dry-run=server"* ]]; then
  echo 'denied by admission policy' >&2
  exit 1
fi
exit 0
""",
    )
    env = os.environ.copy()
    env["PATH"] = f"{tmp_path}:{env['PATH']}"
    return env


def test_ee_saas_deploy_sync_values_and_plan_are_bash3_safe(tmp_path: Path):
    env = _fake_tool_env(tmp_path)
    platform_values = tmp_path / "platform-values.json"
    rendered_file = tmp_path / "rendered.yaml"
    env.update(
        {
            "BROWSER_PILOT_PLATFORM_API_URL": "http://platform.local",
            "BROWSER_PILOT_PLATFORM_TOKEN": "test-token",
            "BROWSER_PILOT_PLATFORM_VALUES_FILE": str(platform_values),
        }
    )

    subprocess.run(["bash", "-n", str(DEPLOY_SCRIPT)], cwd=REPO_ROOT, check=True)
    sync = subprocess.run(
        [str(DEPLOY_SCRIPT), "sync-values"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    assert "wrote platform runtime values" in sync.stderr
    assert platform_values.read_text(encoding="utf-8") == '{"runtime":{"approvedImages":[],"tenants":[]}}'

    env["BROWSER_PILOT_RENDERED_FILE"] = str(rendered_file)
    plan = subprocess.run(
        [str(DEPLOY_SCRIPT), "plan"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    assert plan.returncode == 0
    assert rendered_file.read_text(encoding="utf-8").find(VALID_DIGEST) >= 0


def test_ee_saas_deploy_plan_rejects_placeholder_digest(tmp_path: Path):
    env = _fake_tool_env(tmp_path, rendered_digest="sha256:replace-with-runtime-digest")
    env["BROWSER_PILOT_RENDERED_FILE"] = str(tmp_path / "rendered.yaml")

    result = subprocess.run(
        [str(DEPLOY_SCRIPT), "plan"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert "rendered manifests contain invalid sha256 digest" in result.stderr


def test_ee_saas_deploy_verify_checks_cluster_runtime_baseline(tmp_path: Path):
    env = _fake_tool_env(tmp_path)

    result = subprocess.run(
        [str(DEPLOY_SCRIPT), "verify"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )

    assert result.returncode == 0


def test_ee_saas_chart_locks_session_service_account_and_blocks_container_bypasses():
    runtime_template = (
        REPO_ROOT / "deploy" / "ee-saas" / "chart" / "browser-pilot" / "templates" / "runtime-tenants.yaml"
    ).read_text(encoding="utf-8")
    admission_template = (
        REPO_ROOT / "deploy" / "ee-saas" / "chart" / "browser-pilot" / "templates" / "admission-policy.yaml"
    ).read_text(encoding="utf-8")

    assert "browser-pilot/runtime-session-service-account: \"true\"" in runtime_template
    assert "automountServiceAccountToken: false" in runtime_template
    assert "serviceAccountName" in admission_template
    assert "automountServiceAccountToken" in admission_template
    assert "initContainers" in admission_template
    assert "ephemeralContainers" in admission_template
