#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${LABTETHER_LIVE_HA_QA:-}" != "1" ]]; then
  echo "set LABTETHER_LIVE_HA_QA=1 to run disposable Home Assistant Core QA"
  exit 0
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly REPO_ROOT
readonly HA_IMAGE="ghcr.io/home-assistant/home-assistant@sha256:3a491dcf68a0d17ec439a464f7a076386af11d8aec3e15d1c1c46625783f0340"
readonly SUFFIX="$$-${RANDOM}"
readonly HA_CONTAINER="ltqa-ha-core-${SUFFIX}"
readonly FAKE_CONTAINER="ltqa-ha-fake-${SUFFIX}"
readonly HELPER_CONTAINER="ltqa-ha-helper-${SUFFIX}"
readonly CONFIG_VOLUME="ltqa-ha-config-${SUFFIX}"
readonly TLS_VOLUME="ltqa-ha-tls-${SUFFIX}"
readonly NETWORK="ltqa-ha-network-${SUFFIX}"
TMP_ROOT="${TMPDIR:-/tmp}"
readonly TMP_ROOT="${TMP_ROOT%/}"
TLS_DIR="$(mktemp -d "${TMP_ROOT}/ltqa-ha-tls.XXXXXX")"
readonly TLS_DIR

cleanup() {
  case "${HA_CONTAINER}:${FAKE_CONTAINER}:${HELPER_CONTAINER}:${CONFIG_VOLUME}:${TLS_VOLUME}:${NETWORK}" in
    ltqa-ha-core-*:ltqa-ha-fake-*:ltqa-ha-helper-*:ltqa-ha-config-*:ltqa-ha-tls-*:ltqa-ha-network-*) ;;
    *) echo "refusing unsafe Home Assistant QA cleanup" >&2; return 1 ;;
  esac
  docker rm -f "${HA_CONTAINER}" "${FAKE_CONTAINER}" "${HELPER_CONTAINER}" >/dev/null 2>&1 || true
  docker volume rm "${CONFIG_VOLUME}" "${TLS_VOLUME}" >/dev/null 2>&1 || true
  docker network rm "${NETWORK}" >/dev/null 2>&1 || true
  case "${TLS_DIR}" in
    "${TMP_ROOT}"/ltqa-ha-tls.*) rm -rf -- "${TLS_DIR}" ;;
    *) echo "refusing unsafe TLS fixture cleanup" >&2; return 1 ;;
  esac
}
on_exit() {
  status=$?
  if [[ "${status}" -ne 0 ]] && docker inspect "${HA_CONTAINER}" >/dev/null 2>&1; then
    echo "Home Assistant Core QA failed; final HA log tail follows" >&2
    docker logs --tail 250 "${HA_CONTAINER}" >&2 || true
  fi
  if [[ "${status}" -ne 0 ]] && docker inspect "${FAKE_CONTAINER}" >/dev/null 2>&1; then
    echo "Home Assistant Core QA failed; final fake-hub log tail follows" >&2
    docker logs --tail 100 "${FAKE_CONTAINER}" >&2 || true
  fi
  cleanup
  exit "${status}"
}
trap on_exit EXIT

cleanup
mkdir -p "${TLS_DIR}"
chmod 0700 "${TLS_DIR}"
openssl req \
  -x509 \
  -newkey rsa:2048 \
  -sha256 \
  -nodes \
  -days 1 \
  -subj "/CN=ltqa-ha-fake-hub" \
  -addext "subjectAltName=DNS:ltqa-ha-fake-hub" \
  -keyout "${TLS_DIR}/server.key" \
  -out "${TLS_DIR}/server.crt" \
  >/dev/null 2>&1
chmod 0600 "${TLS_DIR}/server.key"
chmod 0644 "${TLS_DIR}/server.crt"
docker network create "${NETWORK}" >/dev/null
docker volume create "${CONFIG_VOLUME}" >/dev/null
docker volume create "${TLS_VOLUME}" >/dev/null

docker create \
  --name "${HELPER_CONTAINER}" \
  --volume "${CONFIG_VOLUME}:/config" \
  --volume "${TLS_VOLUME}:/tls" \
  --entrypoint /bin/sh \
  "${HA_IMAGE}" \
  -c 'sleep 600' >/dev/null
docker start "${HELPER_CONTAINER}" >/dev/null
docker exec "${HELPER_CONTAINER}" mkdir -p /config/custom_components/labtether
docker cp "${REPO_ROOT}/custom_components/labtether/." "${HELPER_CONTAINER}:/config/custom_components/labtether/"
docker cp "${TLS_DIR}/server.crt" "${HELPER_CONTAINER}:/tls/server.crt"
docker cp "${TLS_DIR}/server.key" "${HELPER_CONTAINER}:/tls/server.key"
docker exec "${HELPER_CONTAINER}" chmod 0600 /tls/server.key
docker exec "${HELPER_CONTAINER}" chmod 0644 /tls/server.crt
docker rm -f "${HELPER_CONTAINER}" >/dev/null

docker run --detach \
  --name "${FAKE_CONTAINER}" \
  --network "${NETWORK}" \
  --network-alias ltqa-ha-fake-hub \
  --publish 127.0.0.1::18080 \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=16m \
  --env LTQA_TLS_CERT=/opt/ltqa/tls/server.crt \
  --env LTQA_TLS_KEY=/opt/ltqa/tls/server.key \
  --volume "${TLS_VOLUME}:/opt/ltqa/tls:ro" \
  --volume "${REPO_ROOT}/tests/live_ha_fake_hub.py:/opt/ltqa/fake_hub.py:ro" \
  --entrypoint python3 \
  "${HA_IMAGE}" \
  /opt/ltqa/fake_hub.py >/dev/null

docker run --detach \
  --name "${HA_CONTAINER}" \
  --network "${NETWORK}" \
  --publish 127.0.0.1::8123 \
  --volume "${CONFIG_VOLUME}:/config" \
  --env TZ=Australia/Sydney \
  "${HA_IMAGE}" >/dev/null

ha_port="$(docker port "${HA_CONTAINER}" 8123/tcp | awk -F: '{print $NF}')"
fake_port="$(docker port "${FAKE_CONTAINER}" 18080/tcp | awk -F: '{print $NF}')"
readonly ha_port fake_port
base_url="http://127.0.0.1:${ha_port}"
fake_external_url="https://127.0.0.1:${fake_port}"
readonly base_url fake_external_url

fake_ready=false
for _ in $(seq 1 80); do
  if curl -kfsS "${fake_external_url}/qa/status" >/dev/null 2>&1; then
    fake_ready=true
    break
  fi
  sleep 0.25
done
if [[ "${fake_ready}" != "true" ]]; then
  echo "disposable fake hub did not become ready" >&2
  docker logs "${FAKE_CONTAINER}" >&2
  exit 1
fi

ha_ready=false
for _ in $(seq 1 360); do
  if curl -fsS "${base_url}/api/onboarding" >/dev/null 2>&1; then
    ha_ready=true
    break
  fi
  if [[ "$(docker inspect --format '{{.State.Running}}' "${HA_CONTAINER}")" != "true" ]]; then
    echo "Home Assistant Core exited during startup" >&2
    docker logs "${HA_CONTAINER}" >&2
    exit 1
  fi
  sleep 0.5
done
if [[ "${ha_ready}" != "true" ]]; then
  echo "Home Assistant Core did not become ready" >&2
  docker logs "${HA_CONTAINER}" >&2
  exit 1
fi

qa_python="${LABTETHER_QA_PYTHON:-}"
if [[ -z "${qa_python}" ]]; then
  if [[ -x "${REPO_ROOT}/.venv/bin/python" ]]; then
    qa_python="${REPO_ROOT}/.venv/bin/python"
  else
    qa_python="python3"
  fi
fi
"${qa_python}" "${REPO_ROOT}/tests/live_ha_core_qa.py" \
  --base-url "${base_url}" \
  --fake-external-url "${fake_external_url}" \
  --fake-internal-url https://ltqa-ha-fake-hub:18080 \
  --ha-container "${HA_CONTAINER}" \
  --fake-container "${FAKE_CONTAINER}"

echo "Home Assistant Core disposable lifecycle test passed"
