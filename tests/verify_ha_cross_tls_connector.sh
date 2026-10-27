#!/usr/bin/env bash
set -Eeuo pipefail

readonly HA_IMAGE="ghcr.io/home-assistant/home-assistant:2026.10.2@sha256:33afb0659d786213264c2211f8d91c5fe87461c56eba6a3a546eb05fc71e89f4"
readonly HA_TLS_URL="${LABTETHER_CROSS_HA_TLS_URL:-https://host.docker.internal:18444}"
readonly HA_TOKEN_FILE="/tmp/labtether-ha-cross-qa-token"
readonly HA_TLS_VOLUME="ltqa-ha-cross-tls-data"
readonly HUB_URL="${LABTETHER_QA_HUB_URL:?set LABTETHER_QA_HUB_URL to the candidate hub HTTPS origin}"
# A disposable Hub API key with the connectors:write scope, in a file only you
# can read. The Hub's shared console token file cannot call connector routes.
readonly HUB_API_KEY_FILE="${LABTETHER_QA_HUB_API_KEY_FILE:?set LABTETHER_QA_HUB_API_KEY_FILE to a file holding a connectors:write Hub API key}"
readonly HUB_CA_FILE="${LABTETHER_QA_HUB_CA_FILE:-}"

if [[ "${HUB_URL}" != https://* ]]; then
  echo "candidate hub URL must use verified HTTPS" >&2
  exit 1
fi
HUB_CURL_CA_ARGS=()
if [[ -n "${HUB_CA_FILE}" ]]; then
  if [[ ! -f "${HUB_CA_FILE}" || -L "${HUB_CA_FILE}" ]]; then
    echo "candidate hub CA must be a regular, non-symlinked file: ${HUB_CA_FILE}" >&2
    exit 1
  fi
  HUB_CURL_CA_ARGS=(--cacert "${HUB_CA_FILE}")
fi

if [[ ! -f "${HA_TOKEN_FILE}" || -L "${HA_TOKEN_FILE}" ]]; then
  echo "missing protected disposable Home Assistant token: ${HA_TOKEN_FILE}" >&2
  exit 1
fi
if [[ ! -f "${HUB_API_KEY_FILE}" || -L "${HUB_API_KEY_FILE}" ]]; then
  echo "Hub API key must be a regular, non-symlinked file: ${HUB_API_KEY_FILE}" >&2
  exit 1
fi
if [[ -n "$(find "${HUB_API_KEY_FILE}" -perm -004 -o -perm -040)" ]]; then
  echo "Hub API key file must not be readable by group or others: ${HUB_API_KEY_FILE}" >&2
  exit 1
fi

# Prove the Docker route itself fails closed before exercising the connector API.
if docker run --rm --entrypoint python3 "${HA_IMAGE}" -c '
import sys
import urllib.request
urllib.request.build_opener(urllib.request.ProxyHandler({})).open(sys.argv[1], timeout=5).read()
' "${HA_TLS_URL}/api/onboarding" >/dev/null 2>&1; then
  echo "untrusted Docker TLS probe unexpectedly succeeded" >&2
  exit 1
fi
docker run --rm \
  --volume "${HA_TLS_VOLUME}:/tls:ro" \
  --entrypoint python3 \
  "${HA_IMAGE}" -c '
import ssl
import sys
import urllib.request
context = ssl.create_default_context(cafile="/tls/ca.pem")
urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    urllib.request.HTTPSHandler(context=context),
).open(sys.argv[1], timeout=5).read()
' "${HA_TLS_URL}/api/onboarding" >/dev/null

umask 077
fail_body="$(mktemp /tmp/ltqa-ha-connector-fail.XXXXXX)"
pass_body="$(mktemp /tmp/ltqa-ha-connector-pass.XXXXXX)"
cleanup() {
  rm -f -- "${fail_body}" "${pass_body}"
}
trap cleanup EXIT

# Authenticate the candidate Hub before reading either bearer token. This
# token-free request proves that the chosen CA and hostname match.
if ! hub_identity="$(
  curl --disable --noproxy '*' --silent --show-error --fail \
    --max-filesize 1048576 \
    --max-time 15 \
    "${HUB_CURL_CA_ARGS[@]}" \
    "${HUB_URL%/}/"
)" \
  || ! jq -e 'type == "object" and .service == "labtether-hub"' \
    <<< "${hub_identity}" >/dev/null; then
  echo "candidate hub TLS or service identity verification failed" >&2
  exit 1
fi

hub_token="$(tr -d '\r\n' < "${HUB_API_KEY_FILE}")"
if [[ -z "${hub_token}" ]]; then
  echo "Hub API key file is empty: ${HUB_API_KEY_FILE}" >&2
  exit 1
fi
ha_token="$(< "${HA_TOKEN_FILE}")"

connector_test() {
  local skip_verify="$1"
  local output_file="$2"
  jq -n \
    --arg base_url "${HA_TLS_URL}" \
    --arg token "${ha_token}" \
    --argjson skip_verify "${skip_verify}" \
    '{base_url:$base_url,token:$token,skip_verify:$skip_verify}' \
    | curl --disable --noproxy '*' --silent --show-error \
      "${HUB_CURL_CA_ARGS[@]}" \
      --output "${output_file}" \
      --write-out '%{http_code}' \
      --header "Authorization: Bearer ${hub_token}" \
      --header 'Content-Type: application/json' \
      --data-binary @- \
      "${HUB_URL%/}/connectors/home-assistant/test"
}

fail_code="$(connector_test false "${fail_body}")"
if [[ "${fail_code}" != "502" ]]; then
  echo "connector without trust returned ${fail_code}, expected fail-closed 502" >&2
  exit 1
fi
if grep -Fq "${ha_token}" "${fail_body}"; then
  echo "connector failure response exposed the Home Assistant token" >&2
  exit 1
fi

pass_code="$(connector_test true "${pass_body}")"
if [[ "${pass_code}" != "200" ]]; then
  echo "connector with skip_verify returned ${pass_code}, expected 200" >&2
  exit 1
fi
jq -e \
  '.status == "ok" and .message == "home assistant API reachable"' \
  "${pass_body}" >/dev/null

echo "Home Assistant connector TLS proof passed (untrusted=${fail_code}, skip_verify=${pass_code})"
