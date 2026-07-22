#!/usr/bin/env bash
set -Eeuo pipefail

readonly HA_IMAGE="ghcr.io/home-assistant/home-assistant@sha256:3a491dcf68a0d17ec439a464f7a076386af11d8aec3e15d1c1c46625783f0340"
readonly HA_TLS_URL="${LABTETHER_CROSS_HA_TLS_URL:-https://host.docker.internal:18444}"
readonly HA_TOKEN_FILE="/tmp/labtether-ha-cross-qa-token"
readonly HA_TLS_VOLUME="ltqa-ha-cross-tls-data"
readonly HUB_CONTAINER="${LABTETHER_QA_HUB_CONTAINER:?set LABTETHER_QA_HUB_CONTAINER to the candidate hub container}"
readonly HUB_URL="${LABTETHER_QA_HUB_URL:?set LABTETHER_QA_HUB_URL to the candidate hub HTTPS origin}"
readonly HUB_TOKEN_PATH="${LABTETHER_QA_HUB_TOKEN_PATH:-/run/labtether/api-token}"

if [[ ! -f "${HA_TOKEN_FILE}" || -L "${HA_TOKEN_FILE}" ]]; then
  echo "missing protected disposable Home Assistant token: ${HA_TOKEN_FILE}" >&2
  exit 1
fi
if ! docker inspect "${HUB_CONTAINER}" >/dev/null 2>&1; then
  echo "candidate hub container not found: ${HUB_CONTAINER}" >&2
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

hub_token="$(docker exec "${HUB_CONTAINER}" sh -c "cat '${HUB_TOKEN_PATH}'")"
ha_token="$(< "${HA_TOKEN_FILE}")"

connector_test() {
  local skip_verify="$1"
  local output_file="$2"
  jq -n \
    --arg base_url "${HA_TLS_URL}" \
    --arg token "${ha_token}" \
    --argjson skip_verify "${skip_verify}" \
    '{base_url:$base_url,token:$token,skip_verify:$skip_verify}' \
    | curl -ksS \
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
