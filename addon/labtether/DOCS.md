# LabTether Home Assistant Add-on

Add `https://github.com/labtether/labtether-homeassistant#homeassistant-addon-repo`
under **Settings > Apps > App store** (**Settings > Add-ons > Add-on Store**
before Home Assistant 2026.2). The branch suffix selects the published add-on
repository layout.

## Web console

Open the web console at `https://<home-assistant-host>:3000`, or use
**Open Web UI** on the add-on page. On first start, create the owner account
on the one-time owner setup screen with the setup token described below.

The console uses the Hub's own certificate, the same one served on port 8443.
That certificate names the add-on's own container addresses, plus the host in
`external_url` when you set it. Set `external_url` to the address you open the
console with, then trust the Hub's certificate authority (download it from
`https://<home-assistant-host>:8443/api/v1/ca.crt`). Until both are done, the
browser shows a certificate warning. Sign-in needs HTTPS, so keep the default `tls_mode`
unless you put your own HTTPS proxy in front of port 3000. That proxy must send
`X-Forwarded-Proto` and the browser's `Host` (or `X-Forwarded-Host`).

The add-on includes the agent installers from the matching Hub release, so you
can add devices from the console. Agents on other machines check the Hub's
certificate against the address they connect to, so set `external_url` before
you add devices.

For the LabTether integration on the same Home Assistant, use the same
`external_url` as the hub URL and the CA file as the CA path.

## Configuration

- `labtether_owner_token`: owner API token (optional when `auto_generate_credentials=true`)
- `labtether_admin_password`: optional local admin password. Leave blank to use the one-time owner setup screen instead of creating the owner automatically.
- `labtether_setup_token`: optional one-time token for the owner setup screen. When blank, a fresh token is generated if `auto_generate_credentials=true`.
- `encryption_key`: base64 key that decodes to 32 bytes (optional when `auto_generate_credentials=true`)
- `database_url`: optional Postgres DSN. Leave blank to use bundled local Postgres in `/data/postgres`.
- `tls_mode`: `auto` (default) or `disabled`.
  - `auto`: the Hub creates its own certificate authority and serves HTTPS on
    port 8443.
  - `disabled`: the Hub serves plain HTTP on port 8080 only, and the console
    plain HTTP on port 3000, for use behind your own HTTPS proxy. Port 8080 is
    not published by default; map it under **Network**.
- `external_url`: the address other machines use to reach the Hub, for
  example `https://192.168.1.20:8443` or `https://homeassistant.local:8443`
  (`https://`, a host and an optional port, no path). The Hub adds that host to
  its certificate and offers this address in **Add device**. Leave it blank and
  browsers and agents on other machines refuse the Hub's certificate, even
  after you trust its certificate authority.
- `auto_generate_credentials`: when enabled, missing required service credentials and first-run setup token are generated securely.

## Generated Credentials

When credentials are generated automatically, they are written to:

- `/data/labtether-addon-root/generated-credentials.txt`

Treat this file as sensitive.

The first-run setup token is kept separately at
`/data/labtether-addon/setup-token` with mode `0600`. It is removed by the hub
after successful owner creation and is never persisted in root-consumed state. A
configured token is staged only once; restarting the add-on cannot resurrect a
previously consumed token.

If the token was used but the database has no owner account, for example after
you point `database_url` at a new empty database, the add-on issues a new setup
token when it starts. With `auto_generate_credentials` turned off, set a new
`labtether_setup_token` option instead.

Secret values are never printed in add-on logs. If you cannot read the setup
token file locally, configure a known strong `labtether_setup_token` option
before first start and remove or rotate the option after owner setup.

## Backups

Home Assistant stops the add-on while it takes a backup (`backup: cold`), so
the bundled PostgreSQL database is copied in a consistent state. The Hub is
offline for the length of the backup.

An add-on backup holds everything needed to restore the Hub:

- the database, including the device credentials the Hub stores encrypted;
- the encryption key and owner token (`/data/labtether-addon-root/`);
- the Hub's certificate authority and private keys (`/data/certs`);
- session recordings and the agent download cache.

Anyone with the backup file can read all of it. Keep backup encryption turned
on in Home Assistant and store backup files as carefully as passwords. With an
external `database_url`, back up that database separately.

## Runtime isolation

The add-on entrypoint performs only the mounted-volume permission bootstrap and
optional local Postgres startup as root. It then starts the LabTether hub as the
dedicated `labtether` account (UID/GID `10001`). Root-consumed state uses strict
JSON under `/data/labtether-addon-root`; only the one-time setup-token directory
is writable by the hub. The hub can also write its install, certificate,
agent-cache, recording, CA-share, and private runtime directories.
`/data/postgres` remains owned by the separate `postgres` account, and the
`/data` volume root remains root-owned and non-listable.

Legacy `runtime.env` state is migrated once by an unprivileged process and is
never executed as root. Secret files remain mode `0600`; private state,
certificate, recording, and runtime directories remain mode `0700`.

Symlinked persisted runtime roots are rejected. The add-on mounts only `/ssl`,
read-only, and does not mount the household Home Assistant configuration or
shared files.

## Networking

Default exposed ports:

- `3000/tcp`: web console (HTTPS)
- `8443/tcp`: Hub API and agent connections (HTTPS)

The internal HTTP redirect listener on port `8080` is not published by default.

## Notes

This add-on runs the LabTether Hub and its web console.
For Home Assistant entities and services, install the custom integration from this repository's `custom_components/labtether` directory.
