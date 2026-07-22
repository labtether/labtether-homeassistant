#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly REPO_ROOT
readonly HUB_IMAGE="ghcr.io/labtether/labtether@sha256:7e317791be6c8df484050316bb77ffad9a454d0d813a9299feb7c08a9911f54f"
readonly ALPINE_IMAGE="alpine:3.19@sha256:6baf43584bcb78f2e5847d1de515f23499913ac9f12bdf834811a3145eb11ca1"
readonly IMAGE_TAG="labtether-ha-addon-security-test:$$-${RANDOM}"
readonly CONTAINER_NAME="labtether-ha-addon-security-$$-${RANDOM}"
readonly DATA_VOLUME="labtether-ha-addon-security-data-$$-${RANDOM}"
OWNER_TOKEN="$(od -An -tx1 -N32 /dev/urandom | tr -d ' \n')"
readonly OWNER_TOKEN
SETUP_TOKEN="$(od -An -tx1 -N32 /dev/urandom | tr -d ' \n')"
readonly SETUP_TOKEN
ENCRYPTION_KEY="$(head -c 32 /dev/urandom | base64 | tr -d '\n')"
readonly ENCRYPTION_KEY

cleanup() {
  docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true
  docker volume rm "${DATA_VOLUME}" >/dev/null 2>&1 || true
  docker image rm -f "${IMAGE_TAG}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker_arch="$(docker info --format '{{.Architecture}}')"
case "${docker_arch}" in
  amd64 | x86_64)
    build_from="ghcr.io/home-assistant/amd64-base:3.23@sha256:e976b27157be0f89fd5bd4757ec6377d2576963623deb135246d0f3c5742f462"
    mutable_build_from="ghcr.io/home-assistant/amd64-base:3.23"
    ;;
  arm64 | aarch64)
    build_from="ghcr.io/home-assistant/aarch64-base:3.23@sha256:a75b07ed8fdccb58720bd844ad9da7a8fc454fc3f3b313ff75ca6fae4b5b821e"
    mutable_build_from="ghcr.io/home-assistant/aarch64-base:3.23"
    ;;
  *)
    echo "unsupported Docker architecture: ${docker_arch}" >&2
    exit 1
    ;;
esac

assert_mutable_reference_rejected() {
  local base_ref="$1"
  local hub_ref="$2"

  if docker build \
    --quiet \
    --target reference-validator \
    --build-arg "BUILD_FROM=${base_ref}" \
    --build-arg "HUB_IMAGE=${hub_ref}" \
    --file "${REPO_ROOT}/addon/labtether/Dockerfile" \
    "${REPO_ROOT}" >/dev/null 2>&1; then
    echo "mutable image reference was unexpectedly accepted" >&2
    exit 1
  fi
}

assert_mutable_reference_rejected "${mutable_build_from}" "${HUB_IMAGE}"
assert_mutable_reference_rejected "${build_from}" "ghcr.io/labtether/labtether:latest"
if docker build \
  --quiet \
  --target reference-validator \
  --build-arg "BUILD_FROM=${build_from}" \
  --file "${REPO_ROOT}/addon/labtether/Dockerfile" \
  "${REPO_ROOT}" >/dev/null 2>&1; then
  echo "missing required HUB_IMAGE reference was unexpectedly accepted" >&2
  exit 1
fi

docker build \
  --build-arg "BUILD_FROM=${build_from}" \
  --build-arg "HUB_IMAGE=${HUB_IMAGE}" \
  --file "${REPO_ROOT}/addon/labtether/Dockerfile" \
  --tag "${IMAGE_TAG}" \
  "${REPO_ROOT}"

docker volume create "${DATA_VOLUME}" >/dev/null
docker run --rm --interactive --volume "${DATA_VOLUME}:/data" "${ALPINE_IMAGE}" sh -c '
  set -eu
  umask 077
  cat > /data/options.json
' <<JSON
{
  "labtether_owner_token": "${OWNER_TOKEN}",
  "labtether_admin_password": "",
  "labtether_setup_token": "${SETUP_TOKEN}",
  "encryption_key": "${ENCRYPTION_KEY}",
  "database_url": "",
  "tls_mode": "auto",
  "auto_generate_credentials": true
}
JSON

docker run --detach \
  --name "${CONTAINER_NAME}" \
  --volume "${DATA_VOLUME}:/data" \
  "${IMAGE_TAG}" >/dev/null

hub_pid=""
for _ in $(seq 1 90); do
  hub_pid="$(docker exec "${CONTAINER_NAME}" pidof labtether 2>/dev/null || true)"
  if [[ -n "${hub_pid}" ]]; then
    break
  fi
  if [[ "$(docker inspect --format '{{.State.Running}}' "${CONTAINER_NAME}")" != "true" ]]; then
    echo "add-on container stopped before the hub started" >&2
    docker logs "${CONTAINER_NAME}" >&2
    exit 1
  fi
  sleep 1
done
if [[ -z "${hub_pid}" ]]; then
  echo "hub process did not start" >&2
  docker logs "${CONTAINER_NAME}" >&2
  exit 1
fi

hub_uid="$(docker exec "${CONTAINER_NAME}" awk '/^Uid:/ { print $2; exit }' "/proc/${hub_pid}/status")"
if [[ -z "${hub_uid}" || "${hub_uid}" == "0" ]]; then
  echo "hub process retained root privileges" >&2
  exit 1
fi
if [[ "${hub_uid}" != "10001" ]]; then
  echo "hub process has unexpected uid ${hub_uid}" >&2
  exit 1
fi

# Seeing the hub process is not enough to prove startup has finished: the hub
# creates its automatic TLS key material after the process becomes visible.
# Wait for the HTTPS health endpoint before inspecting those generated files so
# this gate validates their final ownership instead of racing first boot.
https_ready="false"
for _ in $(seq 1 30); do
  if docker exec --user 10001:10001 "${CONTAINER_NAME}" \
    wget --no-check-certificate --quiet --output-document=- \
    https://127.0.0.1:8443/healthz 2>/dev/null | grep -q '"status":"ok"'; then
    https_ready="true"
    break
  fi
  sleep 1
done
if [[ "${https_ready}" != "true" ]]; then
  echo "hub HTTPS health endpoint did not become ready" >&2
  exit 1
fi

assert_mode_owner() {
  local path="$1"
  local expected="$2"
  local actual
  actual="$(docker exec "${CONTAINER_NAME}" stat -c '%a:%u:%g' "${path}")"
  if [[ "${actual}" != "${expected}" ]]; then
    echo "${path} permissions are ${actual}, expected ${expected}" >&2
    exit 1
  fi
}

assert_mode_owner /data 711:0:0
assert_mode_owner /data/options.json 600:0:0
assert_mode_owner /data/labtether-addon 700:10001:10001
assert_mode_owner /data/labtether-addon/runtime.env 600:10001:10001
assert_mode_owner /data/labtether-addon/setup-token 600:10001:10001
assert_mode_owner /data/labtether-addon/setup-token-option.sha256 600:10001:10001
assert_mode_owner /data/labtether-addon/setup-token-issued.sha256 600:10001:10001
assert_mode_owner /data/install 700:10001:10001
assert_mode_owner /data/certs 700:10001:10001
assert_mode_owner /data/agents 750:10001:10001
assert_mode_owner /data/recordings 700:10001:10001
assert_mode_owner /run/labtether 700:10001:10001
assert_mode_owner /ca 750:10001:10001
assert_mode_owner /data/certs/ca.key 600:10001:10001
assert_mode_owner /data/certs/server.key 600:10001:10001
assert_mode_owner /ca/ca.crt 600:10001:10001

if docker exec "${CONTAINER_NAME}" test -e /usr/bin/tempio; then
  echo "unused vulnerable tempio binary is still present" >&2
  exit 1
fi

docker exec --user 10001:10001 "${CONTAINER_NAME}" sh -c '
  set -eu
  touch \
    /data/install/.write-test \
    /data/certs/.write-test \
    /data/agents/.write-test \
    /data/recordings/.write-test \
    /run/labtether/.write-test \
    /ca/.write-test
  if touch /data/.unexpected-root-write 2>/dev/null; then
    echo "hub user can write to the data volume root" >&2
    exit 1
  fi
  if touch /data/postgres/.unexpected-hub-write 2>/dev/null; then
    echo "hub user can write to the postgres data directory" >&2
    exit 1
  fi
'

container_logs="$(docker logs "${CONTAINER_NAME}" 2>&1)"
for secret in "${OWNER_TOKEN}" "${SETUP_TOKEN}" "${ENCRYPTION_KEY}"; do
  if grep -Fq "${secret}" <<<"${container_logs}"; then
    echo "a configured secret was exposed in container logs" >&2
    exit 1
  fi
done

# Simulate the hub's post-bootstrap token consumption and restart the add-on.
# The issued marker must prevent the option or auto-generation path from
# recreating a setup token, while the bundled Postgres service must restart.
docker exec --user 10001:10001 "${CONTAINER_NAME}" rm /data/labtether-addon/setup-token
docker rm -f "${CONTAINER_NAME}" >/dev/null
docker run --detach \
  --name "${CONTAINER_NAME}" \
  --volume "${DATA_VOLUME}:/data" \
  "${IMAGE_TAG}" >/dev/null

restart_hub_pid=""
for _ in $(seq 1 90); do
  restart_hub_pid="$(docker exec "${CONTAINER_NAME}" pidof labtether 2>/dev/null || true)"
  if [[ -n "${restart_hub_pid}" ]]; then
    break
  fi
  if [[ "$(docker inspect --format '{{.State.Running}}' "${CONTAINER_NAME}")" != "true" ]]; then
    echo "add-on container stopped during restart" >&2
    docker logs "${CONTAINER_NAME}" >&2
    exit 1
  fi
  sleep 1
done
if [[ -z "${restart_hub_pid}" ]]; then
  echo "hub process did not restart" >&2
  docker logs "${CONTAINER_NAME}" >&2
  exit 1
fi

restart_hub_uid="$(docker exec "${CONTAINER_NAME}" awk '/^Uid:/ { print $2; exit }' "/proc/${restart_hub_pid}/status")"
if [[ "${restart_hub_uid}" != "10001" ]]; then
  echo "restarted hub process has unexpected uid ${restart_hub_uid}" >&2
  exit 1
fi
if docker exec "${CONTAINER_NAME}" test -e /data/labtether-addon/setup-token; then
  echo "consumed setup token was recreated on restart" >&2
  exit 1
fi
if ! docker exec "${CONTAINER_NAME}" pg_isready -h 127.0.0.1 -p 5432 -U labtether >/dev/null; then
  echo "bundled Postgres did not restart with persisted local database state" >&2
  exit 1
fi

echo "add-on container security test passed (hub uid=${hub_uid}, restart uid=${restart_hub_uid})"
