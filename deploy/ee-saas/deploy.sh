#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CHART_DIR="${ROOT_DIR}/deploy/ee-saas/chart/browser-pilot"
RELEASE="${BROWSER_PILOT_RELEASE:-browser-pilot-ee-saas}"
NAMESPACE="${BROWSER_PILOT_NAMESPACE:-browser-pilot}"
VALUES_FILE="${BROWSER_PILOT_VALUES:-}"
PLATFORM_VALUES_FILE="${BROWSER_PILOT_PLATFORM_VALUES_FILE:-}"
RENDERED_FILE="${BROWSER_PILOT_RENDERED_FILE:-${TMPDIR:-/tmp}/browser-pilot-ee-saas-rendered.yaml}"
JWT_SECRET_NAME="${BROWSER_PILOT_JWT_SECRET_NAME:-browser-pilot-jwt}"
DATABASE_SECRET_NAME="${BROWSER_PILOT_DATABASE_SECRET_NAME:-browser-pilot-database}"
SKIP_SECRET_CHECK="${BROWSER_PILOT_SKIP_SECRET_CHECK:-false}"
PLATFORM_API_URL="${BROWSER_PILOT_PLATFORM_API_URL:-}"
PLATFORM_TOKEN="${BROWSER_PILOT_PLATFORM_TOKEN:-}"

usage() {
  cat <<'EOF'
Usage: deploy/ee-saas/deploy.sh <plan|apply|verify|status|rollback|sync-values|reconcile-namespaces> [helm args...]

Environment:
  BROWSER_PILOT_RELEASE    Helm release name, default browser-pilot-ee-saas
  BROWSER_PILOT_NAMESPACE  Helm namespace, default browser-pilot
  BROWSER_PILOT_VALUES     Optional values file passed with -f
  BROWSER_PILOT_PLATFORM_VALUES_FILE  Optional generated platform runtime values file
  BROWSER_PILOT_JWT_SECRET_NAME       Backend JWT secret name, default browser-pilot-jwt
  BROWSER_PILOT_DATABASE_SECRET_NAME  Backend DATABASE_URL secret name, default browser-pilot-database
  BROWSER_PILOT_SKIP_SECRET_CHECK     Set true to skip apply-time secret existence checks
  BROWSER_PILOT_PLATFORM_API_URL      Backend URL for deploy audit and sync-values, for example https://browser.example.com
  BROWSER_PILOT_PLATFORM_TOKEN        Platform auth bearer token for deploy audit and sync-values
EOF
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "missing required command: $1" >&2
    exit 127
  }
}

helm_values_args=()
build_helm_values_args() {
  helm_values_args=()
  if [[ -n "${VALUES_FILE}" ]]; then
    helm_values_args+=(-f "${VALUES_FILE}")
  fi
  if [[ -n "${PLATFORM_VALUES_FILE}" ]]; then
    helm_values_args+=(-f "${PLATFORM_VALUES_FILE}")
  fi
}
build_helm_values_args

preflight() {
  require_cmd helm
  require_cmd kubectl
  require_cmd python3
  helm lint "${CHART_DIR}" "${helm_values_args[@]+"${helm_values_args[@]}"}"
  helm template "${RELEASE}" "${CHART_DIR}" \
    --namespace "${NAMESPACE}" \
    "${helm_values_args[@]+"${helm_values_args[@]}"}" \
    >"${RENDERED_FILE}"
  if grep -F "no approved browser-pilot runtime image digests configured" "${RENDERED_FILE}" >/dev/null 2>&1; then
    echo "runtime.approvedImages must include at least one approved image digest" >&2
    exit 2
  fi
  if grep -E "image:[[:space:]]+[^[:space:]]+:latest([[:space:]]|$)" "${RENDERED_FILE}" >/dev/null 2>&1; then
    echo "rendered manifests must not use floating :latest images" >&2
    exit 2
  fi
  RENDERED_FILE="${RENDERED_FILE}" python3 - <<'PY'
import os
import re
import sys

with open(os.environ["RENDERED_FILE"], "r", encoding="utf-8") as fh:
    rendered = fh.read()

digests = sorted(set(re.findall(r"sha256:[A-Za-z0-9._-]+", rendered)))
invalid = [digest for digest in digests if not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest)]
if invalid:
    print(
        "rendered manifests contain invalid sha256 digest(s): " + ", ".join(invalid),
        file=sys.stderr,
    )
    sys.exit(2)
PY
  if ! grep -F "browser-pilot/managed-runtime-namespace" "${RENDERED_FILE}" >/dev/null 2>&1; then
    echo "warning: no tenant runtime namespaces rendered; add runtime.tenants before creating SaaS tenant sessions" >&2
  fi
}

ensure_namespace() {
  kubectl get namespace "${NAMESPACE}" >/dev/null 2>&1 || kubectl create namespace "${NAMESPACE}"
}

check_required_secrets() {
  if [[ "${SKIP_SECRET_CHECK}" == "true" ]]; then
    return
  fi
  ensure_namespace
  kubectl -n "${NAMESPACE}" get secret "${JWT_SECRET_NAME}" >/dev/null
  kubectl -n "${NAMESPACE}" get secret "${DATABASE_SECRET_NAME}" >/dev/null
}

record_deploy_audit() {
  local action="$1"
  local outcome="$2"
  local reason="$3"
  local error="${4:-}"
  if [[ -z "${PLATFORM_API_URL}" || -z "${PLATFORM_TOKEN}" ]]; then
    return
  fi
  if ! command -v curl >/dev/null 2>&1 || ! command -v python3 >/dev/null 2>&1; then
    echo "warning: curl and python3 are required for deploy audit; skipping audit event" >&2
    return
  fi
  local payload
  payload="$(
    ACTION="${action}" OUTCOME="${outcome}" REASON="${reason}" ERROR="${error}" RELEASE="${RELEASE}" NAMESPACE="${NAMESPACE}" \
      python3 - <<'PY'
import json
import os

payload = {
    "action": os.environ["ACTION"],
    "targetType": "deployment",
    "targetId": os.environ["RELEASE"],
    "outcome": os.environ["OUTCOME"],
    "reason": os.environ["REASON"],
    "after": {
        "release": os.environ["RELEASE"],
        "namespace": os.environ["NAMESPACE"],
    },
}
error = os.environ.get("ERROR", "")
if error:
    payload["error"] = error
print(json.dumps(payload, separators=(",", ":")))
PY
  )"
  curl -fsS \
    -X POST "${PLATFORM_API_URL%/}/api/platform/audit-events" \
    -H "Authorization: Bearer ${PLATFORM_TOKEN}" \
    -H "Content-Type: application/json" \
    --data "${payload}" \
    >/dev/null || echo "warning: failed to write platform deploy audit" >&2
}

fetch_platform_runtime_values() {
  if [[ -z "${PLATFORM_API_URL}" || -z "${PLATFORM_TOKEN}" ]]; then
    echo "BROWSER_PILOT_PLATFORM_API_URL and BROWSER_PILOT_PLATFORM_TOKEN are required" >&2
    exit 2
  fi
  require_cmd curl
  if [[ -z "${PLATFORM_VALUES_FILE}" ]]; then
    PLATFORM_VALUES_FILE="${TMPDIR:-/tmp}/browser-pilot-platform-runtime-values.json"
  fi
  curl -fsS \
    -H "Authorization: Bearer ${PLATFORM_TOKEN}" \
    "${PLATFORM_API_URL%/}/api/platform/deploy/runtime-values" \
    >"${PLATFORM_VALUES_FILE}"
  build_helm_values_args
  echo "wrote platform runtime values: ${PLATFORM_VALUES_FILE}" >&2
}

collect_status() {
  echo "----- helm status -----" >&2
  helm status "${RELEASE}" --namespace "${NAMESPACE}" >&2 || true
  echo "----- namespace workload status -----" >&2
  kubectl -n "${NAMESPACE}" get deploy,po,svc,ingress >&2 || true
  echo "----- recent events -----" >&2
  kubectl -n "${NAMESPACE}" get events --sort-by=.lastTimestamp | tail -40 >&2 || true
}

release_fullname() {
  local name="${RELEASE}-browser-pilot"
  name="${name:0:63}"
  while [[ "${name}" == *- ]]; do
    name="${name%-}"
  done
  printf "%s" "${name}"
}

verify_backend_config() {
  local selector="app.kubernetes.io/name=browser-pilot,app.kubernetes.io/component=backend,app.kubernetes.io/instance=${RELEASE}"
  local backend_deploy
  backend_deploy="$(kubectl -n "${NAMESPACE}" get deploy -l "${selector}" -o jsonpath='{.items[0].metadata.name}')"
  if [[ -z "${backend_deploy}" ]]; then
    echo "backend deployment not found for release ${RELEASE} in namespace ${NAMESPACE}" >&2
    exit 2
  fi

  local env_lines
  env_lines="$(kubectl -n "${NAMESPACE}" get deploy "${backend_deploy}" -o jsonpath='{range .spec.template.spec.containers[?(@.name=="backend")].env[*]}{.name}={.value}{"\n"}{end}')"
  for required in "EDITION=ee" "EE_SAAS_MODE=true" "BROWSER_RUNTIME_PROVIDER=kubernetes"; do
    if ! printf "%s\n" "${env_lines}" | grep -Fx "${required}" >/dev/null 2>&1; then
      echo "backend deployment ${backend_deploy} missing required env: ${required}" >&2
      exit 2
    fi
  done
}

verify_runtime_namespace() {
  local runtime_ns="$1"
  kubectl -n "${runtime_ns}" get resourcequota browser-pilot-runtime-quota >/dev/null
  kubectl -n "${runtime_ns}" get limitrange browser-pilot-runtime-limits >/dev/null
  kubectl -n "${runtime_ns}" get networkpolicy browser-pilot-default-deny >/dev/null
  kubectl -n "${runtime_ns}" get networkpolicy browser-pilot-backend-to-session >/dev/null
  kubectl -n "${runtime_ns}" get rolebinding browser-pilot-runtime-provider >/dev/null

  local session_sa
  session_sa="$(kubectl -n "${runtime_ns}" get serviceaccount -l browser-pilot/runtime-session-service-account=true -o jsonpath='{.items[0].metadata.name}')"
  if [[ -z "${session_sa}" ]]; then
    echo "runtime namespace ${runtime_ns} missing locked session service account" >&2
    exit 2
  fi
  local automount
  automount="$(kubectl -n "${runtime_ns}" get serviceaccount "${session_sa}" -o jsonpath='{.automountServiceAccountToken}')"
  if [[ "${automount}" != "false" ]]; then
    echo "runtime namespace ${runtime_ns} service account ${session_sa} must set automountServiceAccountToken=false" >&2
    exit 2
  fi
}

verify_admission_rejects_unsafe_session_pod() {
  local runtime_ns="$1"
  local probe_file
  probe_file="$(mktemp "${TMPDIR:-/tmp}/browser-pilot-admission-probe.XXXXXX.yaml")"
  cat >"${probe_file}" <<'EOF'
apiVersion: v1
kind: Pod
metadata:
  name: browser-pilot-admission-probe
  labels:
    app.kubernetes.io/name: browser-pilot-session
spec:
  hostNetwork: true
  serviceAccountName: default
  automountServiceAccountToken: true
  containers:
    - name: browser
      image: example.invalid/browser-pilot-runtime@sha256:0000000000000000000000000000000000000000000000000000000000000000
      command: ["sh", "-c", "sleep 1"]
EOF
  if kubectl -n "${runtime_ns}" apply --dry-run=server -f "${probe_file}" >/dev/null 2>&1; then
    rm -f "${probe_file}"
    echo "unsafe session pod admission probe was allowed in namespace ${runtime_ns}" >&2
    exit 2
  fi
  rm -f "${probe_file}"
}

verify() {
  require_cmd helm
  require_cmd kubectl
  local fullname
  fullname="$(release_fullname)"
  helm status "${RELEASE}" --namespace "${NAMESPACE}" >/dev/null
  verify_backend_config
  kubectl get validatingadmissionpolicy "${fullname}-runtime-pods" >/dev/null
  kubectl get validatingadmissionpolicybinding "${fullname}-runtime-pods" >/dev/null

  local tenant_namespaces
  tenant_namespaces="$(kubectl get namespace -l browser-pilot/managed-runtime-namespace=true -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}')"
  if [[ -z "${tenant_namespaces}" ]]; then
    echo "warning: no tenant runtime namespaces found; create tenant namespaces before full SaaS runtime validation" >&2
    return
  fi

  local first_runtime_ns=""
  local runtime_ns
  while IFS= read -r runtime_ns; do
    [[ -z "${runtime_ns}" ]] && continue
    [[ -z "${first_runtime_ns}" ]] && first_runtime_ns="${runtime_ns}"
    verify_runtime_namespace "${runtime_ns}"
  done <<<"${tenant_namespaces}"

  if [[ -n "${first_runtime_ns}" ]]; then
    verify_admission_rejects_unsafe_session_pod "${first_runtime_ns}"
  fi
}

plan() {
  preflight
  helm upgrade --install "${RELEASE}" "${CHART_DIR}" \
    --namespace "${NAMESPACE}" \
    --create-namespace \
    --dry-run \
    --debug \
    "${helm_values_args[@]+"${helm_values_args[@]}"}" \
    "$@"
  record_deploy_audit "deploy.plan" "success" "helm dry-run rendered"
}

apply() {
  preflight
  check_required_secrets
  trap 'rc=$?; record_deploy_audit "deploy.apply" "failure" "helm apply failed" "exit_code_${rc}"; collect_status; exit ${rc}' ERR
  helm upgrade --install "${RELEASE}" "${CHART_DIR}" \
    --namespace "${NAMESPACE}" \
    --create-namespace \
    --atomic \
    --wait \
    "${helm_values_args[@]+"${helm_values_args[@]}"}" \
    "$@"
  kubectl -n "${NAMESPACE}" rollout status "deployment/${RELEASE}-browser-pilot-backend" --timeout=180s
  trap - ERR
  record_deploy_audit "deploy.apply" "success" "helm apply completed"
}

status() {
  require_cmd helm
  require_cmd kubectl
  helm status "${RELEASE}" --namespace "${NAMESPACE}"
  kubectl -n "${NAMESPACE}" get deploy,po,svc,ingress
  kubectl get validatingadmissionpolicy,validatingadmissionpolicybinding 2>/dev/null | grep "${RELEASE}" || true
  kubectl get namespace -l browser-pilot/managed-runtime-namespace=true
}

rollback() {
  require_cmd helm
  helm rollback "${RELEASE}" "$@" --namespace "${NAMESPACE}"
  record_deploy_audit "deploy.rollback" "success" "helm rollback completed"
}

sync_values() {
  fetch_platform_runtime_values
}

reconcile_namespaces() {
  fetch_platform_runtime_values
  apply "$@"
  record_deploy_audit "namespace.reconciliation" "success" "helm reconciled platform runtime namespaces"
}

cmd="${1:-}"
if [[ -z "${cmd}" ]]; then
  usage
  exit 2
fi
shift || true

case "${cmd}" in
  plan|dry-run)
    plan "$@"
    ;;
  apply)
    apply "$@"
    ;;
  verify)
    verify "$@"
    ;;
  status)
    status "$@"
    ;;
  rollback)
    rollback "$@"
    ;;
  sync-values)
    sync_values "$@"
    ;;
  reconcile-namespaces)
    reconcile_namespaces "$@"
    ;;
  *)
    usage
    exit 2
    ;;
esac
