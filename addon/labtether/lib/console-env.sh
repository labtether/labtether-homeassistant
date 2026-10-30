#!/usr/bin/env bash
# Shared helpers for the web console s6 services. s6 starts these services
# before run.sh starts the Hub, so each service waits for the files the Hub
# creates at startup.

readonly CONSOLE_OPTIONS_FILE="/data/options.json"
# Used by the service run scripts that source this file.
# shellcheck disable=SC2034
readonly CONSOLE_API_TOKEN_FILE="/run/labtether/api-token"

# Print the configured TLS mode, using the same default as run.sh.
console_tls_mode() {
  local mode

  until [[ -f "${CONSOLE_OPTIONS_FILE}" && ! -L "${CONSOLE_OPTIONS_FILE}" ]]; do
    sleep 2
  done
  mode="$(jq -r '.tls_mode // ""' "${CONSOLE_OPTIONS_FILE}")"
  case "${mode}" in
    "") echo "auto" ;;
    auto | external | disabled) echo "${mode}" ;;
    *)
      echo "console: unsupported tls_mode" >&2
      return 1
      ;;
  esac
}

console_wait_for_file() {
  local path="$1"

  until [[ -s "${path}" ]]; do
    sleep 2
  done
}
