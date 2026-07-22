#!/usr/bin/env bash
set -Eeuo pipefail

readonly OPTIONS_FILE="/data/options.json"
readonly STATE_DIR="/data/labtether-addon"
readonly STATE_ENV_FILE="${STATE_DIR}/runtime.env"
readonly GENERATED_FILE="${STATE_DIR}/generated-credentials.txt"
readonly SETUP_TOKEN_FILE="${STATE_DIR}/setup-token"
readonly SETUP_TOKEN_OPTION_MARKER_FILE="${STATE_DIR}/setup-token-option.sha256"
readonly SETUP_TOKEN_ISSUED_MARKER_FILE="${STATE_DIR}/setup-token-issued.sha256"
readonly HUB_USER="labtether"
readonly HUB_GROUP="labtether"
readonly HUB_INSTALL_DIR="/data/install"
readonly HUB_CERTS_DIR="/data/certs"
readonly HUB_AGENT_CACHE_DIR="/data/agents"
readonly HUB_RECORDINGS_DIR="/data/recordings"
readonly HUB_RUNTIME_DIR="/run/labtether"
readonly HUB_CA_SHARE_DIR="/ca"
readonly HUB_HOME_DIR="/home/labtether"
readonly POSTGRES_RUNTIME_DIR="/run/postgresql"
readonly LOCAL_DATABASE_URL="postgres://labtether@127.0.0.1:5432/labtether?sslmode=disable"

# Preserve process-level configuration before loading the add-on's legacy
# runtime.env file. Older releases persisted generated admin/setup secrets in
# that file; those values must not silently come back after one-time setup.
readonly PROCESS_ADMIN_PASSWORD="${LABTETHER_ADMIN_PASSWORD:-}"
readonly PROCESS_DATABASE_URL="${DATABASE_URL:-}"

log() {
  echo "[labtether-addon] $*"
}

fail() {
  log "ERROR: $*"
  exit 1
}

if [[ "$(id -u)" != "0" ]]; then
  fail "the add-on bootstrap must start as root so it can prepare mounted volumes"
fi
if ! id "${HUB_USER}" >/dev/null 2>&1 || [[ "$(id -u "${HUB_USER}")" == "0" ]]; then
  fail "dedicated unprivileged account ${HUB_USER} is missing or unsafe"
fi

if [[ -L "/data" ]] || [[ ! -d "/data" ]]; then
  fail "/data must be a real directory"
fi
if [[ -L "${STATE_DIR}" ]] || { [[ -e "${STATE_DIR}" ]] && [[ ! -d "${STATE_DIR}" ]]; }; then
  fail "refusing unsafe state directory ${STATE_DIR}"
fi
mkdir -p "${STATE_DIR}"
chmod 700 "${STATE_DIR}"

prepare_owned_tree() {
  local path="$1"
  local mode="$2"

  if [[ -L "${path}" ]] || { [[ -e "${path}" ]] && [[ ! -d "${path}" ]]; }; then
    fail "refusing unsafe runtime directory ${path}"
  fi
  mkdir -p "${path}"
  # -P prevents traversal through symlinked directories in persisted data and
  # -h changes a symlink inode rather than anything it may reference.
  chown -RhP "${HUB_USER}:${HUB_GROUP}" "${path}"
  chmod "${mode}" "${path}"
}

prepare_state_secrets() {
  local path

  prepare_owned_tree "${STATE_DIR}" 0700
  for path in \
    "${STATE_ENV_FILE}" \
    "${GENERATED_FILE}" \
    "${SETUP_TOKEN_FILE}" \
    "${SETUP_TOKEN_OPTION_MARKER_FILE}" \
    "${SETUP_TOKEN_ISSUED_MARKER_FILE}"; do
    if [[ -L "${path}" ]]; then
      fail "refusing symlinked state file ${path}"
    fi
    if [[ -e "${path}" ]]; then
      if [[ ! -f "${path}" ]]; then
        fail "state path ${path} must be a regular file"
      fi
      chown "${HUB_USER}:${HUB_GROUP}" "${path}"
      chmod 600 "${path}"
    fi
  done
}

prepare_hub_runtime() {
  # Keep the volume root owned by root and non-listable. The hub receives only
  # the narrowly scoped subdirectories it must mutate; postgres keeps its own
  # independently owned data tree.
  chown root:root /data
  chmod 0711 /data

  prepare_state_secrets
  prepare_owned_tree "${HUB_INSTALL_DIR}" 0700
  prepare_owned_tree "${HUB_CERTS_DIR}" 0700
  prepare_owned_tree "${HUB_AGENT_CACHE_DIR}" 0750
  prepare_owned_tree "${HUB_RECORDINGS_DIR}" 0700
  prepare_owned_tree "${HUB_RUNTIME_DIR}" 0700
  prepare_owned_tree "${HUB_CA_SHARE_DIR}" 0750
  prepare_owned_tree "${HUB_HOME_DIR}" 0700
  prepare_owned_tree "${HUB_RUNTIME_DIR}/tmp" 0700
}

stage_external_tls_files() {
  local source_cert="${LABTETHER_TLS_CERT:-}"
  local source_key="${LABTETHER_TLS_KEY:-}"
  local staged_cert="${HUB_RUNTIME_DIR}/external-tls/server.crt"
  local staged_key="${HUB_RUNTIME_DIR}/external-tls/server.key"

  if [[ -z "${source_cert}" && -z "${source_key}" ]]; then
    return
  fi
  if [[ -z "${source_cert}" || -z "${source_key}" ]]; then
    fail "LABTETHER_TLS_CERT and LABTETHER_TLS_KEY must be configured together"
  fi
  if [[ -L "${source_cert}" || -L "${source_key}" ]] \
    || [[ ! -f "${source_cert}" || ! -f "${source_key}" ]]; then
    fail "external TLS certificate and key must be regular, non-symlinked files"
  fi

  prepare_owned_tree "${HUB_RUNTIME_DIR}/external-tls" 0700
  install -m 0640 -o "${HUB_USER}" -g "${HUB_GROUP}" "${source_cert}" "${staged_cert}"
  install -m 0600 -o "${HUB_USER}" -g "${HUB_GROUP}" "${source_key}" "${staged_key}"
  export LABTETHER_TLS_CERT="${staged_cert}"
  export LABTETHER_TLS_KEY="${staged_key}"
}

load_state_env() {
  if [[ -L "${STATE_ENV_FILE}" ]] \
    || { [[ -e "${STATE_ENV_FILE}" ]] && [[ ! -f "${STATE_ENV_FILE}" ]]; }; then
    fail "refusing unsafe runtime state file ${STATE_ENV_FILE}"
  fi
  if [[ -f "${STATE_ENV_FILE}" ]]; then
    # shellcheck disable=SC1090
    source "${STATE_ENV_FILE}"
  fi
}

persist_state_value() {
  local key="$1"
  local value="$2"
  local tmp
  tmp="$(mktemp)"
  if [[ -f "${STATE_ENV_FILE}" ]]; then
    grep -v "^${key}=" "${STATE_ENV_FILE}" > "${tmp}" || true
  fi
  printf '%s=%q\n' "${key}" "${value}" >> "${tmp}"
  mv "${tmp}" "${STATE_ENV_FILE}"
  chmod 600 "${STATE_ENV_FILE}"
}

remove_state_value() {
  local key="$1"
  local tmp

  if [[ ! -f "${STATE_ENV_FILE}" ]] || ! grep -q "^${key}=" "${STATE_ENV_FILE}"; then
    return
  fi
  tmp="$(mktemp "${STATE_DIR}/.runtime-env.XXXXXX")"
  grep -v "^${key}=" "${STATE_ENV_FILE}" > "${tmp}" || true
  mv "${tmp}" "${STATE_ENV_FILE}"
  chmod 600 "${STATE_ENV_FILE}"
}

read_option_string() {
  local key="$1"
  jq -r --arg key "${key}" '.[$key] // ""' "${OPTIONS_FILE}"
}

read_option_bool() {
  local key="$1"
  local fallback="$2"
  if [[ ! -f "${OPTIONS_FILE}" ]]; then
    echo "${fallback}"
    return
  fi
  jq -r --arg key "${key}" --arg fallback "${fallback}" '
    if has($key) then
      if .[$key] == true then "true" else "false" end
    else
      $fallback
    end
  ' "${OPTIONS_FILE}"
}

generate_hex_token() {
  od -An -tx1 -N32 /dev/urandom | tr -d ' \n'
}

generate_base64_key() {
  head -c 32 /dev/urandom | base64 | tr -d '\n'
}

require_options_file() {
  if [[ -L "${OPTIONS_FILE}" ]] || [[ ! -f "${OPTIONS_FILE}" ]]; then
    fail "missing ${OPTIONS_FILE}; install via Home Assistant add-on options"
  fi
  chown root:root "${OPTIONS_FILE}"
  chmod 600 "${OPTIONS_FILE}"
}

require_or_generate() {
  local key="$1"
  local option_value="$2"
  local existing_value="$3"
  local auto_generate="$4"
  local generator="$5"
  local output_variable="$6"

  local value
  value="${option_value}"
  if [[ -z "${value}" ]]; then
    value="${existing_value}"
  fi

  if [[ -z "${value}" ]]; then
    if [[ "${auto_generate}" != "true" ]]; then
      fail "${key} is required when auto_generate_credentials=false"
    fi
    value="$(${generator})"
    GENERATED_KEYS+=("${key}")
  fi

  persist_state_value "${key}" "${value}"
  printf -v "${output_variable}" '%s' "${value}"
}

write_secret_file() {
  local path="$1"
  local value="$2"
  local tmp

  if [[ -L "${path}" ]]; then
    fail "refusing to replace symlinked secret file ${path}"
  fi
  umask 077
  tmp="$(mktemp "${STATE_DIR}/.secret.XXXXXX")"
  printf '%s\n' "${value}" > "${tmp}"
  chmod 600 "${tmp}"
  mv "${tmp}" "${path}"
}

validate_setup_token() {
  local value="$1"

  if [[ -z "${value}" ]] || (( ${#value} > 512 )); then
    fail "LABTETHER_SETUP_TOKEN must contain between 1 and 512 characters"
  fi
  if [[ "${value}" == *$'\n'* ]] || [[ "${value}" == *$'\r'* ]]; then
    fail "LABTETHER_SETUP_TOKEN must not contain line breaks"
  fi
}

start_local_postgres() {
  local pgdata="/data/postgres"

  if [[ -L "${pgdata}" ]] || { [[ -e "${pgdata}" ]] && [[ ! -d "${pgdata}" ]]; }; then
    fail "refusing unsafe postgres data directory ${pgdata}"
  fi
  mkdir -p "${pgdata}"
  chown -RhP postgres:postgres "${pgdata}"
  chmod 0700 "${pgdata}"

  if [[ -L "${POSTGRES_RUNTIME_DIR}" ]] \
    || { [[ -e "${POSTGRES_RUNTIME_DIR}" ]] && [[ ! -d "${POSTGRES_RUNTIME_DIR}" ]]; }; then
    fail "refusing unsafe postgres runtime directory ${POSTGRES_RUNTIME_DIR}"
  fi
  mkdir -p "${POSTGRES_RUNTIME_DIR}"
  chown postgres:postgres "${POSTGRES_RUNTIME_DIR}"
  chmod 0750 "${POSTGRES_RUNTIME_DIR}"

  if [[ ! -f "${pgdata}/PG_VERSION" ]]; then
    log "initializing local postgres data directory"
    su-exec postgres initdb -D "${pgdata}" -U labtether --auth=trust >/dev/null
    echo "listen_addresses = '127.0.0.1'" >> "${pgdata}/postgresql.conf"
    echo "port = 5432" >> "${pgdata}/postgresql.conf"
  fi

  log "starting local postgres"
  su-exec postgres postgres -D "${pgdata}" -h 127.0.0.1 -p 5432 >/tmp/labtether-addon-postgres.log 2>&1 &
  PG_PID=$!

  local tries=0
  until pg_isready -h 127.0.0.1 -p 5432 -U labtether >/dev/null 2>&1; do
    tries=$((tries + 1))
    if (( tries > 40 )); then
      fail "local postgres did not become ready"
    fi
    sleep 1
  done

  su-exec postgres createdb -h 127.0.0.1 -p 5432 -U labtether labtether >/dev/null 2>&1 || true
}

write_generated_summary() {
  local tmp

  if (( ${#GENERATED_KEYS[@]} == 0 )); then
    return
  fi
  if [[ -L "${GENERATED_FILE}" ]]; then
    fail "refusing symlinked generated-credentials file ${GENERATED_FILE}"
  fi
  umask 077
  tmp="$(mktemp "${STATE_DIR}/.generated-credentials.XXXXXX")"
  {
    echo "Generated on: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
    echo ""
    for key in "${GENERATED_KEYS[@]}"; do
      echo "${key}=${!key}"
    done
    echo ""
    echo "Store these values securely."
  } > "${tmp}"
  chmod 600 "${tmp}"
  mv "${tmp}" "${GENERATED_FILE}"
  log "generated credentials saved to ${GENERATED_FILE}"
}

cleanup() {
  if [[ -n "${PG_PID:-}" ]] && kill -0 "${PG_PID}" >/dev/null 2>&1; then
    kill "${PG_PID}" >/dev/null 2>&1 || true
    wait "${PG_PID}" || true
  fi
}

trap cleanup EXIT

require_options_file
load_state_env

AUTO_GENERATE="$(read_option_bool "auto_generate_credentials" "true")"
OWNER_TOKEN_OPT="$(read_option_string "labtether_owner_token")"
ADMIN_PASSWORD_OPT="$(read_option_string "labtether_admin_password")"
SETUP_TOKEN_OPT="$(read_option_string "labtether_setup_token")"
ENCRYPTION_KEY_OPT="$(read_option_string "encryption_key")"
DATABASE_URL_OPT="$(read_option_string "database_url")"
TLS_MODE_OPT="$(read_option_string "tls_mode")"

GENERATED_KEYS=()

require_or_generate "LABTETHER_OWNER_TOKEN" "${OWNER_TOKEN_OPT}" "${LABTETHER_OWNER_TOKEN:-}" "${AUTO_GENERATE}" generate_hex_token LABTETHER_OWNER_TOKEN
require_or_generate "LABTETHER_ENCRYPTION_KEY" "${ENCRYPTION_KEY_OPT}" "${LABTETHER_ENCRYPTION_KEY:-}" "${AUTO_GENERATE}" generate_base64_key LABTETHER_ENCRYPTION_KEY

# An absent admin password deliberately selects the one-time local setup flow.
# Never restore the generated admin password used by older add-on releases.
LABTETHER_ADMIN_PASSWORD="${ADMIN_PASSWORD_OPT:-${PROCESS_ADMIN_PASSWORD}}"
remove_state_value "LABTETHER_ADMIN_PASSWORD"
remove_state_value "LABTETHER_SETUP_TOKEN"

SETUP_TOKEN_GENERATED="false"
if [[ -n "${LABTETHER_ADMIN_PASSWORD}" ]]; then
  # A configured password bootstraps the owner directly, so a dormant setup
  # token would only add secret material without enabling a useful flow.
  rm -f "${SETUP_TOKEN_FILE}"
else
  SETUP_TOKEN_OPTION_HASH=""
  SETUP_TOKEN_OPTION_MARKER=""
  SETUP_TOKEN_ISSUED_MARKER=""
  if [[ -n "${SETUP_TOKEN_OPT}" ]]; then
    validate_setup_token "${SETUP_TOKEN_OPT}"
    SETUP_TOKEN_OPTION_HASH="$(printf '%s' "${SETUP_TOKEN_OPT}" | sha256sum | awk '{print $1}')"
  fi
  if [[ -f "${SETUP_TOKEN_OPTION_MARKER_FILE}" && ! -L "${SETUP_TOKEN_OPTION_MARKER_FILE}" ]]; then
    SETUP_TOKEN_OPTION_MARKER="$(tr -d ' \r\n' < "${SETUP_TOKEN_OPTION_MARKER_FILE}")"
  fi
  if [[ -f "${SETUP_TOKEN_ISSUED_MARKER_FILE}" && ! -L "${SETUP_TOKEN_ISSUED_MARKER_FILE}" ]]; then
    SETUP_TOKEN_ISSUED_MARKER="$(tr -d ' \r\n' < "${SETUP_TOKEN_ISSUED_MARKER_FILE}")"
  fi

  if [[ -n "${SETUP_TOKEN_OPTION_HASH}" && "${SETUP_TOKEN_OPTION_HASH}" != "${SETUP_TOKEN_OPTION_MARKER}" ]]; then
    # Stage a newly configured operator token once. The hash marker prevents a
    # consumed token from being resurrected on every add-on restart.
    LABTETHER_SETUP_TOKEN="${SETUP_TOKEN_OPT}"
    write_secret_file "${SETUP_TOKEN_FILE}" "${LABTETHER_SETUP_TOKEN}"
    write_secret_file "${SETUP_TOKEN_OPTION_MARKER_FILE}" "${SETUP_TOKEN_OPTION_HASH}"
    write_secret_file "${SETUP_TOKEN_ISSUED_MARKER_FILE}" "${SETUP_TOKEN_OPTION_HASH}"
  elif [[ -f "${SETUP_TOKEN_FILE}" && ! -L "${SETUP_TOKEN_FILE}" ]]; then
    LABTETHER_SETUP_TOKEN="$(tr -d '\r\n' < "${SETUP_TOKEN_FILE}")"
    validate_setup_token "${LABTETHER_SETUP_TOKEN}"
    chmod 600 "${SETUP_TOKEN_FILE}"
    if [[ -z "${SETUP_TOKEN_ISSUED_MARKER}" ]]; then
      write_secret_file "${SETUP_TOKEN_ISSUED_MARKER_FILE}" \
        "$(printf '%s' "${LABTETHER_SETUP_TOKEN}" | sha256sum | awk '{print $1}')"
    fi
    log "reusing pending first-run setup token at ${SETUP_TOKEN_FILE}"
  elif [[ -n "${SETUP_TOKEN_ISSUED_MARKER}" || -n "${SETUP_TOKEN_OPTION_MARKER}" ]]; then
    # The hub removes the token after the first owner transaction commits. The
    # issued marker prevents a restart from silently creating a new bootstrap
    # secret after that one-time credential has been consumed.
    if [[ -z "${SETUP_TOKEN_ISSUED_MARKER}" ]]; then
      write_secret_file "${SETUP_TOKEN_ISSUED_MARKER_FILE}" "${SETUP_TOKEN_OPTION_MARKER}"
    fi
    log "first-run setup token was previously issued and is no longer pending"
  else
    if [[ "${AUTO_GENERATE}" != "true" ]]; then
      fail "labtether_setup_token is required when no admin password is configured and auto_generate_credentials=false"
    fi
    LABTETHER_SETUP_TOKEN="$(generate_hex_token)"
    write_secret_file "${SETUP_TOKEN_FILE}" "${LABTETHER_SETUP_TOKEN}"
    write_secret_file "${SETUP_TOKEN_ISSUED_MARKER_FILE}" \
      "$(printf '%s' "${LABTETHER_SETUP_TOKEN}" | sha256sum | awk '{print $1}')"
    SETUP_TOKEN_GENERATED="true"
  fi
fi

if [[ -n "${DATABASE_URL_OPT}" ]]; then
  DATABASE_URL="${DATABASE_URL_OPT}"
  persist_state_value "DATABASE_URL" "${DATABASE_URL}"
  persist_state_value "DATABASE_MODE" "external"
elif [[ -n "${PROCESS_DATABASE_URL}" ]]; then
  DATABASE_URL="${PROCESS_DATABASE_URL}"
  persist_state_value "DATABASE_URL" "${DATABASE_URL}"
  persist_state_value "DATABASE_MODE" "external"
else
  DATABASE_URL="${DATABASE_URL:-}"
fi

if [[ -z "${DATABASE_URL_OPT}" && -z "${PROCESS_DATABASE_URL}" ]] \
  && { [[ -z "${DATABASE_URL}" ]] || [[ "${DATABASE_MODE:-}" == "local" ]] \
    || [[ "${DATABASE_URL}" == "${LOCAL_DATABASE_URL}" ]]; }; then
  start_local_postgres
  DATABASE_URL="${LOCAL_DATABASE_URL}"
  persist_state_value "DATABASE_URL" "${DATABASE_URL}"
  persist_state_value "DATABASE_MODE" "local"
fi
if [[ -z "${DATABASE_URL}" ]]; then
  fail "database_url resolved to an empty value"
fi

if [[ -z "${TLS_MODE_OPT}" ]]; then
  TLS_MODE_OPT="auto"
fi

# Export runtime env expected by LabTether hub.
export API_PORT=8080
export LABTETHER_HTTP_PORT=8080
export LABTETHER_HTTPS_PORT=8443
export LABTETHER_TLS_MODE="${TLS_MODE_OPT}"
export LABTETHER_DATA_DIR=/data
export LABTETHER_AGENT_CACHE_DIR="${HUB_AGENT_CACHE_DIR}"
export LABTETHER_CA_SHARE_DIR="${HUB_CA_SHARE_DIR}"
export LABTETHER_ENV=production
export LABTETHER_OWNER_TOKEN
export LABTETHER_API_TOKEN="${LABTETHER_OWNER_TOKEN}"
export LABTETHER_ADMIN_PASSWORD
export LABTETHER_ENCRYPTION_KEY
export DATABASE_URL
if [[ -z "${LABTETHER_ADMIN_PASSWORD}" ]]; then
  export LABTETHER_SETUP_TOKEN_FILE="${SETUP_TOKEN_FILE}"
else
  unset LABTETHER_SETUP_TOKEN_FILE LABTETHER_SETUP_TOKEN || true
fi

# Ensure base64 key decodes to 32 bytes.
if ! decoded_len=$(printf '%s' "${LABTETHER_ENCRYPTION_KEY}" | base64 -d 2>/dev/null | wc -c | tr -d ' '); then
  fail "LABTETHER_ENCRYPTION_KEY must be valid base64"
fi
if [[ "${decoded_len}" != "32" ]]; then
  fail "LABTETHER_ENCRYPTION_KEY must decode to 32 bytes"
fi

write_generated_summary
prepare_hub_runtime
stage_external_tls_files

export HOME="${HUB_HOME_DIR}"
export TMPDIR="${HUB_RUNTIME_DIR}/tmp"

if [[ "${SETUP_TOKEN_GENERATED}" == "true" ]]; then
  log "generated a one-time first-run setup token at ${SETUP_TOKEN_FILE}; token values are never written to logs"
  log "configure labtether_setup_token before first start when the token must be known without local file access"
fi

log "starting LabTether hub as ${HUB_USER} (uid=$(id -u "${HUB_USER}"), tls_mode=${LABTETHER_TLS_MODE})"
cd /
exec su-exec "${HUB_USER}:${HUB_GROUP}" /usr/local/bin/labtether
