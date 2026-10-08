#!/usr/bin/env bash
# Root-owned persistence, validation, and migration helpers for run.sh.

state_json_is_valid() {
  local path="$1"

  jq -e '
    type == "object"
    and ((keys - [
      "DATABASE_MODE",
      "DATABASE_URL",
      "LABTETHER_ENCRYPTION_KEY",
      "LABTETHER_OWNER_TOKEN"
    ]) | length == 0)
    and all(.[]; type == "string")
    and (
      (.DATABASE_MODE // "") == ""
      or (.DATABASE_MODE // "") == "local"
      or (.DATABASE_MODE // "") == "external"
    )
  ' "${path}" >/dev/null 2>&1
}

write_state_json() {
  local value="$1"
  local tmp

  umask 077
  tmp="$(mktemp "${ROOT_STATE_DIR}/.runtime-json.XXXXXX")"
  printf '%s\n' "${value}" > "${tmp}"
  if ! state_json_is_valid "${tmp}"; then
    rm -f -- "${tmp}"
    fail "refusing invalid runtime state"
  fi
  chown root:root "${tmp}"
  chmod 600 "${tmp}"
  mv "${tmp}" "${STATE_JSON_FILE}"
}

migrate_legacy_state() {
  local legacy_json

  if [[ -f "${STATE_JSON_FILE}" ]]; then
    return
  fi
  if [[ ! -e "${LEGACY_STATE_ENV_FILE}" ]]; then
    write_state_json '{}'
    return
  fi
  if [[ -L "${LEGACY_STATE_ENV_FILE}" ]] || [[ ! -f "${LEGACY_STATE_ENV_FILE}" ]]; then
    fail "refusing unsafe legacy runtime state file ${LEGACY_STATE_ENV_FILE}"
  fi

  # Older releases persisted Bash-escaped assignments. Execute that legacy
  # syntax only as the already-exposed Hub user, then cross back to root through
  # one strictly validated JSON document containing the four supported fields.
  # The quoted scripts expand only after the UID drop.
  # shellcheck disable=SC2016
  if ! legacy_json="$(
    su-exec "${HUB_USER}:${HUB_GROUP}" /usr/bin/env -i PATH=/usr/bin:/bin \
      /bin/bash --noprofile --norc -c '
        set -Eeuo pipefail
        # shellcheck disable=SC1090
        source "$1"
        /usr/bin/env -i \
          STATE_OWNER_TOKEN="${LABTETHER_OWNER_TOKEN:-}" \
          STATE_ENCRYPTION_KEY="${LABTETHER_ENCRYPTION_KEY:-}" \
          STATE_DATABASE_URL="${DATABASE_URL:-}" \
          STATE_DATABASE_MODE="${DATABASE_MODE:-}" \
          /bin/bash --noprofile --norc -c '\''
            exec /usr/bin/jq -n \
              --arg owner "${STATE_OWNER_TOKEN}" \
              --arg encryption "${STATE_ENCRYPTION_KEY}" \
              --arg database_url "${STATE_DATABASE_URL}" \
              --arg database_mode "${STATE_DATABASE_MODE}" \
              "{LABTETHER_OWNER_TOKEN: \$owner, LABTETHER_ENCRYPTION_KEY: \$encryption, DATABASE_URL: \$database_url, DATABASE_MODE: \$database_mode}"
          '\''
      ' _ "${LEGACY_STATE_ENV_FILE}"
  )"; then
    fail "could not migrate legacy runtime state"
  fi
  write_state_json "${legacy_json}"
  rm -f -- "${LEGACY_STATE_ENV_FILE}"
}

# These globals are consumed by the sourcing entrypoint after state loading.
# shellcheck disable=SC2034
load_state_json() {
  if [[ -L "${STATE_JSON_FILE}" ]] || [[ ! -f "${STATE_JSON_FILE}" ]]; then
    fail "missing safe runtime state file ${STATE_JSON_FILE}"
  fi
  if ! state_json_is_valid "${STATE_JSON_FILE}"; then
    fail "refusing invalid runtime state file ${STATE_JSON_FILE}"
  fi

  local value
  value="$(jq -r '.LABTETHER_OWNER_TOKEN // ""' "${STATE_JSON_FILE}")"
  if [[ -n "${value}" ]]; then
    LABTETHER_OWNER_TOKEN="${value}"
  fi
  value="$(jq -r '.LABTETHER_ENCRYPTION_KEY // ""' "${STATE_JSON_FILE}")"
  if [[ -n "${value}" ]]; then
    LABTETHER_ENCRYPTION_KEY="${value}"
  fi
  value="$(jq -r '.DATABASE_URL // ""' "${STATE_JSON_FILE}")"
  if [[ -n "${value}" ]]; then
    DATABASE_URL="${value}"
  fi
  value="$(jq -r '.DATABASE_MODE // ""' "${STATE_JSON_FILE}")"
  if [[ -n "${value}" ]]; then
    DATABASE_MODE="${value}"
  fi
}

persist_state_value() {
  local key="$1"
  local value="$2"
  local updated

  case "${key}" in
    LABTETHER_OWNER_TOKEN | LABTETHER_ENCRYPTION_KEY | DATABASE_URL | DATABASE_MODE) ;;
    *) fail "refusing unsupported runtime state key ${key}" ;;
  esac
  updated="$(jq --arg key "${key}" --arg value "${value}" '.[$key] = $value' "${STATE_JSON_FILE}")"
  write_state_json "${updated}"
}
