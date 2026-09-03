# LabTether Home Assistant Add-on

This add-on runs the LabTether hub runtime inside Home Assistant.

## What This Add-on Provides

- Starts the LabTether hub binary (`cmd/labtether`) inside the add-on container.
- Supports either:
  - external Postgres via `database_url`, or
  - bundled local Postgres initialized in `/data/postgres` when `database_url` is empty.
- Persists root-consumed credentials under `/data/labtether-addon-root/` and
  exposes only the one-time setup token under `/data/labtether-addon/`.

## Required / Recommended Options

- `labtether_owner_token` (optional when `auto_generate_credentials=true`)
- `labtether_admin_password` (optional; leave blank to use one-time owner setup)
- `labtether_setup_token` (optional when `auto_generate_credentials=true`; used only by one-time owner setup)
- `encryption_key` (optional when `auto_generate_credentials=true`; must decode to 32 bytes)
- `database_url` (optional; leave empty for local Postgres)
- `tls_mode` (`auto`, `external`, `disabled`)
- `auto_generate_credentials` (`true` recommended for first install)

## Generated Credentials

When auto-generation is enabled and required values are missing, the add-on writes generated values to:

- `/data/labtether-addon-root/generated-credentials.txt`

Treat this file as sensitive.

When no admin password is configured, the first-run setup token is kept in
`/data/labtether-addon/setup-token` until the hub consumes it. It is not copied
into root-consumed runtime state, and an option-supplied token is staged only
once so an old token cannot reappear after restart.

The add-on never prints secret values in logs. For the most operator-friendly
first-run flow, set a known strong `labtether_setup_token` option before first
start, then remove or rotate that option after owner setup.

## Runtime security

The entrypoint uses root only for mounted-volume bootstrap and optional local
Postgres startup. The hub itself runs as the dedicated `labtether` user with
UID/GID `10001`. Root-consumed state is strict JSON in a root-owned directory;
legacy shell state is migrated only as UID `10001`. The hub can write the
one-time setup-token, install, certificate, agent-cache, recording, CA-share,
and private runtime directories. The data-volume root remains root-owned, while
local Postgres keeps separate ownership of `/data/postgres`.

Container base and hub image inputs must include an immutable `sha256` digest.
The release workflow resolves the versioned hub image to its registry digest
and fails before building if it cannot obtain one.

## Notes

- This add-on package currently targets the LabTether hub runtime and API endpoints.
- For Home Assistant entity/sensor integration, continue using the custom integration in this repository's `custom_components/labtether` directory.

## Release Automation

- Workflow: `.github/workflows/addon-release.yml`.
- Produces:
  - GHCR images per architecture (`labtether-homeassistant-addon-amd64`, `labtether-homeassistant-addon-aarch64`),
  - repository layout artifacts (`dist/ha-addon-repository` + tarball),
  - hosted repository branch `homeassistant-addon-repo`.
