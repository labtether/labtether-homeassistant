# LabTether Home Assistant add-on

This add-on runs the LabTether hub inside Home Assistant.

## What it does

- Starts the LabTether hub binary (`cmd/labtether`) inside the add-on container.
- Serves the LabTether web console over HTTPS on port `3000`, including the
  one-time owner setup screen.
- Includes the agent installers from the matching Hub release.
- Supports either:
  - external Postgres via `database_url`, or
  - bundled local Postgres initialized in `/data/postgres` when `database_url` is empty.
- Keeps generated credentials under `/data/labtether-addon-root/` and only
  the one-time setup token under `/data/labtether-addon/`.

## Options

- `labtether_owner_token` (optional when `auto_generate_credentials=true`)
- `labtether_admin_password` (optional; leave blank to use one-time owner setup)
- `labtether_setup_token` (optional when `auto_generate_credentials=true`; used only by one-time owner setup)
- `encryption_key` (optional when `auto_generate_credentials=true`; must decode to 32 bytes)
- `database_url` (optional; leave empty for local Postgres)
- `tls_mode` (`auto`, `disabled`)
- `external_url` (optional; the address other machines use to reach the Hub)
- `auto_generate_credentials` (`true` recommended for first install)

## Generated credentials

When auto-generation is enabled and required values are missing, the add-on writes generated values to:

- `/data/labtether-addon-root/generated-credentials.txt`

Treat this file as sensitive.

When no admin password is configured, the first-run setup token is kept in
`/data/labtether-addon/setup-token` until the hub uses it. It is not copied
into the state that root reads, and a token from the options is staged only
once, so a used token cannot come back after a restart.

The add-on never prints secret values in logs. To finish owner setup, set a
strong `labtether_setup_token` option and restart the add-on (before or after
the first start), then remove it after owner setup.

## Runtime security

The start script uses root only to set permissions on the mounted volumes and
to start the optional local Postgres. The hub itself runs as the `labtether`
user with UID/GID `10001`. The state that root reads is strict JSON in a
root-owned directory; older shell state is migrated only as UID `10001`. The
hub can write the one-time setup-token, install, certificate, agent-cache,
recording, CA-share, and private runtime directories. The data-volume root remains root-owned, while
local Postgres keeps separate ownership of `/data/postgres`.

Container base and hub image inputs must include an immutable `sha256` digest.
The release workflow resolves the versioned hub image to its registry digest
and fails before building if it cannot obtain one.

## Notes

- The web console and Hub API use the Hub's own certificate on ports `3000` and `8443`.
- For LabTether devices, sensors and power switches in Home Assistant, install
  the LabTether integration from this repository's
  `custom_components/labtether` folder.

## Release automation

- Workflow: `.github/workflows/addon-release.yml`. It runs only from a
  `vX.Y.Z` tag. Only the newest release moves the `latest` image tags and the
  hosted repository branch.
- Produces:
  - GHCR images per architecture (`labtether-homeassistant-addon-amd64`, `labtether-homeassistant-addon-aarch64`),
  - repository layout artifacts (`dist/ha-addon-repository` + tarball),
  - hosted repository branch `homeassistant-addon-repo`.
