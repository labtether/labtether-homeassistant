# Changelog

## 2.0.0

Runs LabTether Hub 2.0.0, which has the new 2.0 web console. Ports are
unchanged from 1.7.1.

Changes:

- New `external_url` option: the address other machines use to reach the Hub.
  The Hub adds its host to its certificate and offers it in **Add device**.
  Before, the certificate named only the add-on's container addresses, so
  agents on other machines, and browsers that trusted the Hub's certificate
  authority, still refused the Hub at the Home Assistant host's address.

- When the setup token was already used but the database has no owner account,
  for example after `database_url` points at a new empty database, the add-on
  now issues a new setup token at start. Before, the Hub stopped at start and
  the add-on restarted in a loop. With `auto_generate_credentials` turned off,
  the add-on stops at start and its log says to set a new
  `labtether_setup_token` option.

- The add-on description and docs use plain words, and the add-on README lists
  the `external_url` option.

- The add-on's options have names and descriptions on its **Configuration**
  tab. It showed the raw option keys, such as `labtether_owner_token`.

- With `external_url` set, the Hub names its HTTPS console at that host on
  port 3000. It named `http://...:3000`, which the add-on's console refuses.

- The add-on log no longer shows the s6-overlay warning "defining user bundles
  in /etc/s6-overlay/s6-rc.d is deprecated" at every start.

- The first-start log and the docs say how to finish owner setup in Home
  Assistant: set `labtether_setup_token` and restart the add-on. They said to
  set it before the first start or to read a file in the add-on's `/data`,
  which Home Assistant does not show. Setting it after the first start works.

- Stopping the add-on stops the Hub first, then the bundled Postgres. Both
  got the stop signal together, so the log showed "live runtime lease release
  failed" at every stop.

## 1.7.1

Runs LabTether Hub 1.7.1.

Check before upgrading:

- The add-on now mounts only `/ssl`, read-only, and no longer mounts Home
  Assistant's `/config` or `/share` folders.
- `tls_mode: external` is removed. The add-on had no way to supply the
  certificate, so the Hub could not start in that mode. Switch to `auto`.
- Home Assistant now stops the add-on while it takes a backup, so the bundled
  database is saved in a consistent state. The Hub is offline during backups.
- Port `8080` (HTTP redirect and health listener) is no longer published. Use
  `https://<home-assistant-host>:8443`.
- Generated credentials are now stored in
  `/data/labtether-addon-root/generated-credentials.txt`.

Changes:

- The add-on now includes the web console, served over HTTPS on port `3000`
  with the Hub's own certificate. Earlier builds had no console, so the owner
  setup screen could not be reached.
- The add-on now includes the agent installers from the matching Hub release.
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
