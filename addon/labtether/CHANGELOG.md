# Changelog

## 1.7.0

Runs LabTether Hub 1.7.0.

Check before upgrading:

- External TLS certificate and key files must live under `/ssl`. The add-on now
  mounts only `/ssl`, read-only, and no longer mounts Home Assistant's
  `/config` or `/share` folders.
- Port `8080` (HTTP redirect and health listener) is no longer published. Use
  `https://<home-assistant-host>:8443`.
- Generated credentials are now stored in
  `/data/labtether-addon-root/generated-credentials.txt`.

Changes:

- Added the `labtether_setup_token` option and a one-time setup token for
  creating the owner account in the browser. A used token cannot come back
  after a restart.
- The Hub now runs as a dedicated unprivileged account (UID/GID 10001). Only
  the volume permission setup and the bundled Postgres start as root.
- Saved add-on state is stored as strict JSON and is never run as a script.
  Older `runtime.env` state is migrated once. Secrets are not written to logs.
- Supervisor settings such as external TLS paths now reach the add-on at
  startup.
- Images are built from digest-pinned base and Hub images, include provenance
  and SBOM attestations, and are published only after an anonymous pull check
  succeeds.
- Updated the Home Assistant base image and Alpine packages to their current
  security releases, including c-ares 1.34.9 and a CA bundle without the
  withdrawn emSign root.

## 0.1.0-alpha

- Added runnable experimental Home Assistant add-on runtime for LabTether hub.
- Added startup option validation and auto-generated credential persistence.
- Added bundled local Postgres fallback when `database_url` is not set.
- Added release packaging support for hosted add-on repository publication.
