#!/bin/bash
# The hub-stop service's down script. On add-on stop, s6 brings the console
# services down, then runs this, and only then sends every remaining process
# TERM. The Hub (the container's main command) and the bundled Postgres used to
# get that TERM together, so Postgres dropped the Hub's connection before the
# Hub released its runtime lease ("live runtime lease release failed").
# Stop the Hub first, wait for it, then shut Postgres down cleanly.
set -u

log() { printf '[labtether-addon] %s\n' "$*"; }

wait_gone() { # pid, tenths of a second
  local pid="$1" tries="$2"
  while (( tries > 0 )) && kill -0 "${pid}" 2>/dev/null; do
    sleep 0.1
    tries=$((tries - 1))
  done
  ! kill -0 "${pid}" 2>/dev/null
}

# The Hub runs as UID 10001 (run.sh drops to it before exec).
hub_pid=""
for pid in $(pidof labtether 2>/dev/null); do
  if [[ "$(awk '/^Uid:/ { print $2; exit }' "/proc/${pid}/status" 2>/dev/null)" == "10001" ]]; then
    hub_pid="${pid}"
    break
  fi
done
if [[ -n "${hub_pid}" ]]; then
  log "stopping LabTether hub"
  kill -TERM "${hub_pid}" 2>/dev/null || true
  # The Supervisor waits 10 s before it kills the add-on; keep both steps under it.
  wait_gone "${hub_pid}" 60 || log "hub still running after 6 s"
fi

pid_file="/data/postgres/postmaster.pid"
if [[ -f "${pid_file}" && ! -L "${pid_file}" ]]; then
  pg_pid="$(head -n 1 "${pid_file}")"
  if [[ "${pg_pid}" =~ ^[0-9]+$ ]] && kill -0 "${pg_pid}" 2>/dev/null; then
    log "stopping local postgres"
    # SIGINT is Postgres' fast shutdown: end sessions, checkpoint, exit.
    kill -INT "${pg_pid}" 2>/dev/null || true
    wait_gone "${pg_pid}" 30 || log "local postgres still running after 3 s"
  fi
fi
exit 0
