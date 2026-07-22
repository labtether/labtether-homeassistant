# LabTether Home Assistant Add-on

## Configuration

- `labtether_owner_token`: owner API token (optional when `auto_generate_credentials=true`)
- `labtether_admin_password`: optional local admin password. Leave blank to use the one-time owner setup screen instead of creating the owner automatically.
- `labtether_setup_token`: optional one-time token for the owner setup screen. When blank, a fresh token is generated if `auto_generate_credentials=true`.
- `encryption_key`: base64 key that decodes to 32 bytes (optional when `auto_generate_credentials=true`)
- `database_url`: optional Postgres DSN. Leave blank to use bundled local Postgres in `/data/postgres`.
- `tls_mode`: `auto`, `external`, or `disabled`
- `auto_generate_credentials`: when enabled, missing required service credentials and first-run setup token are generated securely.

## Generated Credentials

When credentials are generated automatically, they are written to:

- `/data/labtether-addon/generated-credentials.txt`

Treat this file as sensitive.

The first-run setup token is kept separately at
`/data/labtether-addon/setup-token` with mode `0600`. It is removed by the hub
after successful owner creation and is never persisted in `runtime.env`. A
configured token is staged only once; restarting the add-on cannot resurrect a
previously consumed token.

Secret values are never printed in add-on logs. If you cannot read the setup
token file locally, configure a known strong `labtether_setup_token` option
before first start and remove or rotate the option after owner setup.

## Runtime isolation

The add-on entrypoint performs only the mounted-volume permission bootstrap and
optional local Postgres startup as root. It then starts the LabTether hub as the
dedicated `labtether` account (UID/GID `10001`). The hub can write only its
state, install, certificate, agent-cache, recording, CA-share, and private
runtime directories. `/data/postgres` remains owned by the separate `postgres`
account, and the `/data` volume root remains root-owned and non-listable.

Existing state in those dedicated directories is migrated to the unprivileged
account on first start. Secret files remain mode `0600`; private state,
certificate, recording, and runtime directories remain mode `0700`.

When external TLS files are configured through `LABTETHER_TLS_CERT` and
`LABTETHER_TLS_KEY`, the root bootstrap copies them into the private runtime
directory with restrictive permissions before dropping privileges. Symlinked
TLS inputs and symlinked persisted runtime roots are rejected.

## Networking

Default exposed port:

- `8443/tcp` (HTTPS)

The internal HTTP redirect listener on port `8080` is not published by default.

## Notes

This add-on runs the LabTether hub runtime.
For Home Assistant entities/services integration, install the custom integration from this repository's `custom_components/labtether` directory.
