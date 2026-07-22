#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly REPO_ROOT
readonly HA_IMAGE="ghcr.io/home-assistant/home-assistant@sha256:3a491dcf68a0d17ec439a464f7a076386af11d8aec3e15d1c1c46625783f0340"
readonly HA_CONTAINER="ltqa-ha-cross-core"
readonly HELPER_CONTAINER="ltqa-ha-cross-helper"
readonly CONFIG_VOLUME="ltqa-ha-cross-config"
readonly NETWORK="ltqa-ha-cross-network"
readonly HA_PORT="${LABTETHER_CROSS_HA_PORT:-18123}"
readonly TLS_PROXY_CONTAINER="ltqa-ha-cross-tls"
readonly TLS_HELPER_CONTAINER="ltqa-ha-cross-tls-helper"
readonly TLS_VOLUME="ltqa-ha-cross-tls-data"
readonly TLS_PORT="${LABTETHER_CROSS_HA_TLS_PORT:-18444}"
readonly TLS_DIR="/tmp/labtether-ha-cross-tls"
readonly TLS_CA_FILE="${TLS_DIR}/ca.pem"
readonly TOKEN_FILE="/tmp/labtether-ha-cross-qa-token"
readonly QA_USERNAME="ltqa-cross"
readonly QA_PASSWORD="LTqa-cross-product-2026!"

cleanup_tls() {
  case "${TLS_PROXY_CONTAINER}:${TLS_HELPER_CONTAINER}:${TLS_VOLUME}:${TLS_DIR}" in
    ltqa-ha-cross-tls:ltqa-ha-cross-tls-helper:ltqa-ha-cross-tls-data:/tmp/labtether-ha-cross-tls) ;;
    *) echo "refusing unsafe cross-product TLS cleanup" >&2; return 1 ;;
  esac
  docker rm -f "${TLS_PROXY_CONTAINER}" "${TLS_HELPER_CONTAINER}" >/dev/null 2>&1 || true
  docker volume rm "${TLS_VOLUME}" >/dev/null 2>&1 || true
  rm -rf -- "${TLS_DIR}"
}

cleanup() {
  case "${HA_CONTAINER}:${HELPER_CONTAINER}:${CONFIG_VOLUME}:${NETWORK}:${TOKEN_FILE}" in
    ltqa-ha-cross-core:ltqa-ha-cross-helper:ltqa-ha-cross-config:ltqa-ha-cross-network:/tmp/labtether-ha-cross-qa-token) ;;
    *) echo "refusing unsafe cross-product cleanup" >&2; return 1 ;;
  esac
  cleanup_tls
  docker rm -f "${HA_CONTAINER}" "${HELPER_CONTAINER}" >/dev/null 2>&1 || true
  docker volume rm "${CONFIG_VOLUME}" >/dev/null 2>&1 || true
  docker network rm "${NETWORK}" >/dev/null 2>&1 || true
  rm -f -- "${TOKEN_FILE}"
}

show_status() {
  if docker inspect "${HA_CONTAINER}" >/dev/null 2>&1; then
    docker inspect --format 'container={{.Name}} state={{.State.Status}}' "${HA_CONTAINER}"
  else
    echo "container=${HA_CONTAINER} state=absent"
  fi
  if docker inspect "${TLS_PROXY_CONTAINER}" >/dev/null 2>&1; then
    docker inspect --format 'tls_container={{.Name}} state={{.State.Status}}' "${TLS_PROXY_CONTAINER}"
  else
    echo "tls_container=${TLS_PROXY_CONTAINER} state=absent"
  fi
  echo "host_url=http://127.0.0.1:${HA_PORT}"
  echo "docker_url=http://host.docker.internal:${HA_PORT}"
  echo "tls_host_url=https://127.0.0.1:${TLS_PORT}"
  echo "tls_docker_url=https://host.docker.internal:${TLS_PORT}"
  if [[ -f "${TOKEN_FILE}" && ! -L "${TOKEN_FILE}" ]]; then
    echo "token_file=${TOKEN_FILE} state=ready"
  else
    echo "token_file=${TOKEN_FILE} state=absent"
  fi
  if [[ -f "${TLS_CA_FILE}" && ! -L "${TLS_CA_FILE}" ]]; then
    echo "tls_ca_file=${TLS_CA_FILE} state=ready"
  else
    echo "tls_ca_file=${TLS_CA_FILE} state=absent"
  fi
}

start_tls() {
  if [[ "$(docker inspect --format '{{.State.Running}}' "${HA_CONTAINER}" 2>/dev/null || true)" != "true" ]]; then
    echo "start the disposable Home Assistant instance before its TLS route" >&2
    exit 1
  fi
  if ! [[ "${TLS_PORT}" =~ ^[0-9]+$ ]] || (( TLS_PORT < 1024 || TLS_PORT > 65535 )); then
    echo "invalid LABTETHER_CROSS_HA_TLS_PORT=${TLS_PORT}" >&2
    exit 1
  fi
  if ! docker inspect "${TLS_PROXY_CONTAINER}" >/dev/null 2>&1 \
    && curl -kfsS --max-time 1 "https://127.0.0.1:${TLS_PORT}/" >/dev/null 2>&1; then
    echo "port ${TLS_PORT} is already serving HTTPS outside the QA proxy" >&2
    exit 1
  fi

  cleanup_tls
  install -d -m 700 "${TLS_DIR}"
  (
    umask 077
    openssl req -x509 -newkey rsa:2048 -nodes -sha256 -days 8 \
      -subj "/CN=LabTether Disposable HA QA CA" \
      -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
      -addext "keyUsage=critical,keyCertSign,cRLSign" \
      -keyout "${TLS_DIR}/ca-key.pem" \
      -out "${TLS_CA_FILE}" >/dev/null 2>&1
    openssl req -new -newkey rsa:2048 -nodes -sha256 \
      -subj "/CN=host.docker.internal" \
      -addext "subjectAltName=DNS:host.docker.internal,DNS:${TLS_PROXY_CONTAINER},IP:127.0.0.1" \
      -addext "basicConstraints=critical,CA:FALSE" \
      -addext "keyUsage=critical,digitalSignature,keyEncipherment" \
      -addext "extendedKeyUsage=serverAuth" \
      -keyout "${TLS_DIR}/server-key.pem" \
      -out "${TLS_DIR}/server.csr" >/dev/null 2>&1
    openssl x509 -req -sha256 -days 8 \
      -in "${TLS_DIR}/server.csr" \
      -CA "${TLS_CA_FILE}" \
      -CAkey "${TLS_DIR}/ca-key.pem" \
      -CAcreateserial \
      -copy_extensions copy \
      -out "${TLS_DIR}/server.pem" >/dev/null 2>&1
    rm -f -- "${TLS_DIR}/ca-key.pem" "${TLS_DIR}/ca.srl" "${TLS_DIR}/server.csr"
    chmod 600 "${TLS_CA_FILE}" "${TLS_DIR}/server.pem" "${TLS_DIR}/server-key.pem"
  )

  docker volume create "${TLS_VOLUME}" >/dev/null
  docker create \
    --name "${TLS_HELPER_CONTAINER}" \
    --volume "${TLS_VOLUME}:/tls" \
    --entrypoint /bin/sh \
    "${HA_IMAGE}" \
    -c 'sleep 600' >/dev/null
  docker start "${TLS_HELPER_CONTAINER}" >/dev/null
  docker cp "${TLS_CA_FILE}" "${TLS_HELPER_CONTAINER}:/tls/ca.pem"
  docker cp "${TLS_DIR}/server.pem" "${TLS_HELPER_CONTAINER}:/tls/server.pem"
  docker cp "${TLS_DIR}/server-key.pem" "${TLS_HELPER_CONTAINER}:/tls/server-key.pem"
  docker cp "${REPO_ROOT}/tests/ha_core_tls_proxy.py" "${TLS_HELPER_CONTAINER}:/tls/ha_core_tls_proxy.py"
  docker exec "${TLS_HELPER_CONTAINER}" sh -c \
    'chown -R 1000:1000 /tls && chmod 700 /tls && chmod 400 /tls/*.pem && chmod 500 /tls/ha_core_tls_proxy.py'
  docker rm -f "${TLS_HELPER_CONTAINER}" >/dev/null

  docker run --detach \
    --name "${TLS_PROXY_CONTAINER}" \
    --network "${NETWORK}" \
    --publish "127.0.0.1:${TLS_PORT}:8443" \
    --volume "${TLS_VOLUME}:/tls:ro" \
    --read-only \
    --tmpfs /tmp:rw,noexec,nosuid,size=16m \
    --cap-drop ALL \
    --security-opt no-new-privileges \
    --user 1000:1000 \
    --env PYTHONDONTWRITEBYTECODE=1 \
    --entrypoint python3 \
    "${HA_IMAGE}" \
    /tls/ha_core_tls_proxy.py \
      --cert /tls/server.pem \
      --key /tls/server-key.pem \
      --upstream "http://${HA_CONTAINER}:8123" >/dev/null

  tls_ready=false
  for _ in $(seq 1 120); do
    if curl -fsS --cacert "${TLS_CA_FILE}" \
      --resolve "host.docker.internal:${TLS_PORT}:127.0.0.1" \
      "https://host.docker.internal:${TLS_PORT}/api/onboarding" >/dev/null 2>&1; then
      tls_ready=true
      break
    fi
    if [[ "$(docker inspect --format '{{.State.Running}}' "${TLS_PROXY_CONTAINER}")" != "true" ]]; then
      echo "Home Assistant TLS proxy stopped during startup" >&2
      docker logs "${TLS_PROXY_CONTAINER}" >&2
      exit 1
    fi
    sleep 0.25
  done
  if [[ "${tls_ready}" != "true" ]]; then
    echo "Home Assistant TLS proxy did not become ready" >&2
    docker logs "${TLS_PROXY_CONTAINER}" >&2
    exit 1
  fi

  if curl -fsS --resolve "host.docker.internal:${TLS_PORT}:127.0.0.1" \
    "https://host.docker.internal:${TLS_PORT}/api/onboarding" >/dev/null 2>&1; then
    echo "untrusted Home Assistant TLS route unexpectedly passed verification" >&2
    exit 1
  fi

  tls_ca_mode="$(stat -f '%Lp' "${TLS_CA_FILE}" 2>/dev/null || stat -c '%a' "${TLS_CA_FILE}")"
  if [[ "${tls_ca_mode}" != "600" ]]; then
    echo "TLS CA file has mode ${tls_ca_mode}, expected 600" >&2
    exit 1
  fi

  echo "Home Assistant TLS route ready; untrusted verification failed closed"
  echo "tls_local_url=https://127.0.0.1:${TLS_PORT}"
  echo "tls_docker_url=https://host.docker.internal:${TLS_PORT}"
  echo "tls_ca_file=${TLS_CA_FILE} mode=${tls_ca_mode}"
}

start() {
  if ! [[ "${HA_PORT}" =~ ^[0-9]+$ ]] || (( HA_PORT < 1024 || HA_PORT > 65535 )); then
    echo "invalid LABTETHER_CROSS_HA_PORT=${HA_PORT}" >&2
    exit 1
  fi
  if curl -fsS --max-time 1 "http://127.0.0.1:${HA_PORT}/" >/dev/null 2>&1; then
    echo "port ${HA_PORT} is already serving HTTP; stop that service first" >&2
    exit 1
  fi

  cleanup
  docker network create "${NETWORK}" >/dev/null
  docker volume create "${CONFIG_VOLUME}" >/dev/null
  docker create \
    --name "${HELPER_CONTAINER}" \
    --volume "${CONFIG_VOLUME}:/config" \
    --entrypoint /bin/sh \
    "${HA_IMAGE}" \
    -c 'sleep 600' >/dev/null
  docker start "${HELPER_CONTAINER}" >/dev/null
  docker exec "${HELPER_CONTAINER}" mkdir -p /config/custom_components/labtether
  docker cp \
    "${REPO_ROOT}/custom_components/labtether/." \
    "${HELPER_CONTAINER}:/config/custom_components/labtether/"
  docker rm -f "${HELPER_CONTAINER}" >/dev/null

  docker run --detach \
    --name "${HA_CONTAINER}" \
    --network "${NETWORK}" \
    --publish "127.0.0.1:${HA_PORT}:8123" \
    --volume "${CONFIG_VOLUME}:/config" \
    --env TZ=Australia/Sydney \
    "${HA_IMAGE}" >/dev/null

  ready=false
  for _ in $(seq 1 360); do
    if curl -fsS "http://127.0.0.1:${HA_PORT}/api/onboarding" >/dev/null 2>&1; then
      ready=true
      break
    fi
    if [[ "$(docker inspect --format '{{.State.Running}}' "${HA_CONTAINER}")" != "true" ]]; then
      echo "Home Assistant stopped during cross-product startup" >&2
      docker logs "${HA_CONTAINER}" >&2
      exit 1
    fi
    sleep 0.5
  done
  if [[ "${ready}" != "true" ]]; then
    echo "Home Assistant did not become ready" >&2
    docker logs "${HA_CONTAINER}" >&2
    exit 1
  fi

  "${REPO_ROOT}/.venv/bin/python" "${REPO_ROOT}/tests/prepare_ha_cross_qa.py" \
    --base-url "http://127.0.0.1:${HA_PORT}" \
    --username "${QA_USERNAME}" \
    --password "${QA_PASSWORD}" \
    --token-output "${TOKEN_FILE}"

  start_tls
  show_status
  echo "qa_username=${QA_USERNAME}"
  echo "qa_password=${QA_PASSWORD}"
  echo "candidate containers can reach HA at http://host.docker.internal:${HA_PORT}"
  echo "or attach the candidate to ${NETWORK} and use http://${HA_CONTAINER}:8123"
}

case "${1:-status}" in
  start) start ;;
  stop) cleanup; show_status ;;
  restart) cleanup; start ;;
  tls-start) start_tls; show_status ;;
  tls-stop) cleanup_tls; show_status ;;
  tls-restart) cleanup_tls; start_tls; show_status ;;
  status) show_status ;;
  *) echo "usage: $0 {start|stop|restart|status|tls-start|tls-stop|tls-restart}" >&2; exit 2 ;;
esac
