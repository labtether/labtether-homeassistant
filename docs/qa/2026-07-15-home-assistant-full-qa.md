# Home Assistant integration and add-on QA — 2026-07-15

## Result

The LabTether custom integration completed a hands-on browser journey and a
repeatable disposable Home Assistant Core `2026.7.2` lifecycle. The add-on
completed an installed-container lifecycle using its real hub binary and local
Postgres. All automated, live, packaging, and static gates listed below pass.

This does **not** claim a production Home Assistant install: the read-only
inspection of the Simba household found no LabTether config entry, HACS
integration, entities, add-on, or LabTether log entries. Simba itself reported a
valid configuration, no active repairs, and healthy/supported status. It was not
mutated during this QA.

## Hands-on integration journey

Using Home Assistant's visible web UI against a disposable Core instance:

1. Completed Home Assistant onboarding and opened **Settings > Devices &
   services > Add integration > LabTether**.
2. Entered a deliberately wrong API key and confirmed the form showed the
   specific rejected-key message. The retry retained the non-secret URL, name,
   and TLS choices while clearing the key.
3. Connected to a disposable HTTPS hub with a self-signed certificate. The
   certificate failed closed until **Ignore TLS certificate errors** was enabled.
4. Confirmed the setup preview reported 2 assets, 2 telemetry-capable assets, 2
   controllable assets, 2 active alerts, and the Docker/Proxmox sources.
5. Enabled status, telemetry, power, and the optional action service at a
   five-second polling interval. The review screen rendered each choice as a
   separate Markdown list row.
6. Confirmed Home Assistant created 3 devices and 13 entities. The VM device
   showed firmware `8.4`, CPU `11.5%`, memory `22.5%`, disk `33.5%`, connected
   status, and power control.
7. Operated the VM power switch and confirmed the fake hub received the exact
   bounded `vm.stop` action.
8. Stopped the hub and confirmed the VM status, metrics, and control became
   unavailable; restarted it and confirmed values recovered.
9. Disabled telemetry and the action service in **Configure**. After the fix,
   the integration exposed 5 live entities and the VM device exposed only
   status and power; disabled telemetry rows no longer remained as unavailable
   registry ghosts.
10. Exercised reconfigure, reload, disable/enable, a Home Assistant restart, and
    config-entry removal. Removal left no LabTether entry or entity behind.

The browser console emitted generic Home Assistant frontend transition/error
parsing messages during navigation, but Home Assistant's backend logs contained
no LabTether exception. The only integration messages were the standard custom
component warning and the deliberately induced coordinator outage.

## Defects fixed during QA

- Removed entity-registry ghosts when an operator disables a LabTether entity
  category.
- Corrected legacy unique-ID migration to derive the entity domain from the
  entity ID; Home Assistant records the integration name, not the entity domain,
  in the registry `platform` field.
- Preserved non-secret connection fields after connection/authentication errors
  while clearing the API key, including reconfiguration.
- Rendered the review summary as readable Markdown rows instead of one collapsed
  paragraph.
- Extended the live harness to prove self-signed TLS rejection/override and
  registry cleanup through Home Assistant's real APIs.

## Add-on installed-container journey

`./tests/addon_container_security_test.sh` built the add-on from digest-pinned
Home Assistant and LabTether images and then ran the actual hub plus bundled
Postgres. It proved:

- mutable or missing image references are rejected;
- the hub runs as UID `10001`, not root;
- options, generated runtime secrets, TLS private keys, and writable directories
  have the expected ownership and restrictive modes;
- configured secrets do not appear in logs;
- HTTPS `/healthz` becomes ready;
- the hub user can write only to its scoped runtime paths;
- a consumed one-time setup token is not recreated; and
- a full container restart preserves and restarts local Postgres.

Supervisor store installation was not performed on Simba because LabTether is
not installed there and this QA maintained a no-production-mutation boundary.

## Reproducible evidence

```text
.venv/bin/python -m pytest tests/ -v
100 passed

LABTETHER_LIVE_HA_QA=1 ./tests/ha_core_live_test.sh
Home Assistant Core live integration QA passed
Home Assistant Core disposable lifecycle test passed

./tests/addon_container_security_test.sh
add-on container security test passed (hub uid=10001, restart uid=10001)

shellcheck addon/labtether/run.sh scripts/release/package-ha-addon-repo.sh \
  tests/addon_container_security_test.sh tests/ha_core_live_test.sh \
  tests/ha_core_cross_product_instance.sh
passed

actionlint
passed
```

The release packaging script was also run with QA version
`1.2.0-qa.20260715`; its repository index, architecture-qualified image name,
config version, and tar contents were validated, then the temporary artifact was
removed.

## Disposable cross-product target

The final hub-connector pass can target the fully onboarded, isolated Home
Assistant instance without touching Simba:

- host URL: `http://127.0.0.1:18123`
- URL from a Docker container: `http://host.docker.internal:18123`
- local HTTPS URL: `https://127.0.0.1:18444`
- HTTPS URL from a Docker container:
  `https://host.docker.internal:18444`
- same-network URL: `http://ltqa-ha-cross-core:8123` on
  `ltqa-ha-cross-network`
- seven-day token file: `/tmp/labtether-ha-cross-qa-token` (verified mode
  `0600`; do not print or commit it)
- disposable CA PEM: `/tmp/labtether-ha-cross-tls/ca.pem` (verified mode
  `0600`)

`tls-start` and `tls-restart` generate a fresh disposable chain; clients using
explicit CA trust must reread the PEM after either command.

```bash
./tests/ha_core_cross_product_instance.sh status
./tests/ha_core_cross_product_instance.sh restart
./tests/ha_core_cross_product_instance.sh tls-restart
./tests/ha_core_cross_product_instance.sh stop
```

The HTTP route remains available alongside HTTPS. A Docker client without the
disposable CA failed certificate validation; the same client succeeded when
given the CA. The proxy runs read-only as UID `1000`, with all Linux capabilities
dropped and `no-new-privileges`, and forwards to the unchanged HA Core target.

The target starts with no LabTether config entry so the candidate hub's Home
Assistant connector can be evaluated against a clean instance. Run the exact
connector proof without printing either token:

```bash
LABTETHER_QA_HUB_CONTAINER=<hub-container> \
LABTETHER_QA_HUB_URL=https://<hub-host>:<port> \
  ./tests/verify_ha_cross_tls_connector.sh
```

The verifier requires the connector to return `502` with normal certificate
verification and `200` only with its explicit `skip_verify` setting. Home
Assistant has no connector CA field at this revision, so `skip_verify` is used
only for this isolated, self-signed QA endpoint.

This was run against installed hub candidate `labtetherqar14-labtether-1` at
`https://192.168.0.118:28443`:

```text
Home Assistant connector TLS proof passed (untrusted=502, skip_verify=200)
```

The candidate log recorded the first request failing with `x509: certificate
signed by unknown authority`, followed by an audited `200` for the explicit
`skip_verify` request. The first candidate attempt also found and fixed a QA
proxy defect: its HTTP client had decompressed Home Assistant's gzip response
while retaining the encoding header. The proxy now forwards encoded bytes
unchanged, and the same two-request proof passes.

## Final installed Hub connector journey

The source-level TLS verifier above was followed by an operator-style browser
journey ending on final installed Hub candidate
`labtether-installed-qa:20260715-r22`
(`sha256:95e215d40f293fa5f41a757d65ae487f232ac9fbf6531bd2398c5aa89958c0a9`).
From **Add Device > Home Assistant**, the operator entered the disposable HTTPS
endpoint and token, saw the normal certificate-verification failure, explicitly
enabled the isolated self-signed-certificate override, and saved the connector.
The console reported `Home Assistant connector saved`; a manual sync started,
and Devices showed the Home Assistant hub plus 17 discovered entity assets
across eight domains.

Stopping the disposable Home Assistant target changed the installed console's
connection test to the contextual error `Failed to test Home Assistant
connection. Check the endpoint, credentials, network access, and TLS settings.`
Restarting it restored `home assistant API reachable`, and **Run Sync** again
reported `collector run started`. The connector configuration and all 17
discovered assets survived the in-place r17-to-r18-to-r20-to-r21-to-r22
candidate upgrades.

R21's loaded restart exposed a separate Hub lifecycle failure: a transient
runtime-lease loss let canceled PBS work outlive the bounded drain and write
through a closing database pool. That candidate was rejected. R22 contains the
cancellation and resource-fencing repair and repeated the proof from the
installed product. Its owner session, connector, Home Assistant hub asset, and
17 entity assets survived both the in-place upgrade and a deliberate restart.
The controller returned online, refreshed again during the five-minute watch,
and remained online while 661 post-restart Hub requests completed with zero
HTTP 4xx/5xx. No drain timeout, lease, closed-pool, fatal, panic, OOM, or
automatic-restart signal appeared. Seven child entities remained offline only
because their actual upstream Home Assistant states were unavailable/unknown;
that truthful entity state is not a connector outage. The disposable Home
Assistant instance remained unchanged, and Simba was not mutated.

## r26 final installed Hub connector re-certification — 2026-07-16

The final user-level pass supersedes r22 and r25 as the installed-candidate
result. It ran against exact image `labtether-installed-qa:20260716-r26`,
image/digest
`sha256:09a833de6ced3f1cb1b943cf94939e080452db78ef6b57e1b78c704cd3c61af2`,
with the private-CA-validated `/version` route returning HTTP 200 and
`20260716-r26`.
The production `build/Dockerfile` was rebuilt for Linux arm64 without cache;
`npm ci` reported zero vulnerabilities and the production Next.js build and
typecheck completed successfully.

Only the Hub, web console, and console ingress containers were replaced.
PostgreSQL and its volumes were retained, and database readback stayed at
migration high-water/count `96/96`, one user, 21 assets, two collectors, and
five sessions. The three r26 application containers became healthy with exact
r26 image identity, `RestartCount=0`, and `OOMKilled=false`.

A fresh installed-console browser journey proved the final behavior:

- Baseline showed the Home Assistant connector **Online** at exact Base URL
  `https://host.docker.internal:18444`, with 17 entities across eight domains.
  Seven entities were truthfully unavailable in upstream Home Assistant; they
  were not misreported as a connector outage.
- **Refresh** completed with the visible status `Home Assistant data refreshed
  from the connector.`
- Stopping only the disposable `ltqa-ha-cross-tls` target and pressing
  **Refresh** immediately changed the device header to **Offline** and showed:
  `Unable to reach Home Assistant. Check that it is online and that the
  connector Base URL and network path are correct, then try again. Showing data
  from the last successful sync.` The 17 discovered entities remained visible
  as explicitly stale last-successful-sync data rather than disappearing or
  being presented as fresh success.
- Restarting that disposable target and pressing **Refresh** immediately
  restored **Online**, the success message, and a fresh **Last Seen** value.

This pass found and repaired two user-visible Hub defects. A Home Assistant
refresh now performs a real connector fetch rather than only updating the
shared status model, and the device identity header gives explicit unhealthy
connector state precedence over a recently cached `lastSeen`. The latter was
found in the installed r24 outage exercise, repaired in r25, and re-proved in
the exact r26 candidate.

The final source regression evidence passed:

- the full Hub collector test package;
- the focused Home Assistant collector race test;
- Home Assistant console Vitest: 2 of 2 tests;
- device-status badge Vitest: 3 of 3 tests;
- console TypeScript and scoped ESLint; and
- `git diff --check` for the repaired change set.

Final disposition: **PASS for the installed r26 Home Assistant connector's
normal refresh, honest outage/stale-data presentation, and immediate recovery
journey**. The no-mutation statement for Simba remains unchanged.

## r27 final Hub re-certification — 2026-07-17

The final installed image was rebuilt without cache from the hardened Hub
source as `labtether-installed-qa:20260717-r27`, exact image ID
`sha256:7f088d78bfbb26b6a350096110fce2105ba6cd022192f15cc3d6534e5d0e0940`.
Only `labtether`, `web-console`, and `console-ingress` were recreated. The
PostgreSQL container retained ID
`d4fd5c5478ef59cf99b7d0923e3c6da2b358dbdaf63a6d49fba8af74ebb038f3`,
its original start time, restart count zero, and all 21 assets. Strict
private-CA requests to `/version` and `/healthz` returned HTTP 200 with version
`20260717-r27` and PostgreSQL `ok`.

The exact r27 installed console repeated the user failure/recovery journey:

- live **Refresh** showed **Online**, 17 entities across eight domains, and a
  fresh collector snapshot;
- stopping only disposable `ltqa-ha-cross-tls` and pressing **Refresh** changed
  the header to **Offline**, displayed the actionable Home Assistant reachability
  message, and explicitly retained all 17 entities as last-successful-sync data;
- restarting that disposable proxy and pressing **Refresh** restored **Online**
  immediately, with no stale error banner and the full inventory intact.

At the 33-minute installed checkpoint, all three r27 application containers
were healthy with restart count zero and `OOMKilled=false`; the fatal-log scan
found no lease loss, incomplete drain, panic, fatal, OOM, or shutdown error.
The earlier long r26 observation is not counted as a clean soak because Mac
clamshell sleep suspended Colima at each recorded lease-loss time. A fresh
24-hour r27 check remains scheduled on the exact image and is not pre-claimed.

Final disposition: **PASS for the exact installed r27 Home Assistant normal,
outage, cached-data, and recovery journey**. Production Simba remained
read-only and unmodified.

## r27 24-hour checkpoint — 2026-07-21

Checkpoint result: **FAIL / blocker for the exact-r27 24-hour installed soak**.

The checkpoint was run at `2026-07-21T15:53:18Z`, after the scheduled
`2026-07-16T16:15:00Z` r27 observation start. The installed Docker runtime was
not running: `docker context show` selected `colima`, `docker ps` failed because
`/Users/michael/.colima/default/docker.sock` was absent, and `colima status`
reported `colima is not running`. The default Docker context was also
unavailable at `/var/run/docker.sock`.

Strict endpoint checks did not reach the installed hub: `https://192.168.0.118:28443/version`
and `/healthz` both timed out, and the local console port `http://localhost:3000`
refused connection. The Colima host-agent log shows the VM entered
`VirtualMachineStateError` at `2026-07-16T21:49:29+10:00` and socket forwarding
was stopped at `2026-07-16T21:49:37+10:00`. Colima was not restarted for this
checkpoint because doing so would turn the soak into a restart/recovery event.

Because the runtime was down, this checkpoint cannot confirm the three
`labtetherqar17` application containers still used image
`sha256:7f088d78bfbb26b6a350096110fce2105ba6cd022192f15cc3d6534e5d0e0940`,
were healthy, had `RestartCount=0`, had `OOMKilled=false`, or had no fatal,
panic, lease-loss, incomplete-drain, closed-pool, or shutdown-error signature
since `2026-07-16T16:15:00Z`. It also cannot confirm the preserved PostgreSQL
container ID, start time, restart count, database, `/version`, `/healthz`, or
current Home Assistant console inventory. No production Home Assistant
connector, credential, or household automation was changed.

## r27 24-hour follow-up checkpoint - 2026-07-22

Checkpoint result: **FAIL / blocker; the exact-r27 24-hour installed soak
remains unproven**.

This follow-up checkpoint was run at `2026-07-22T02:21:48Z`. The current Docker
context was `default`, but `/var/run/docker.sock` was absent and `docker ps`
could not connect. The Colima Docker socket
`/Users/michael/.colima/default/docker.sock` was also absent, `colima status`
reported `colima is not running`, and no `Docker`, `colima`, `lima`, `qemu`, or
`vz` process was visible to `pgrep` at the checkpoint.

Strict endpoint checks again did not reach the installed hub:
`https://192.168.0.118:28443/version` and `/healthz` timed out with HTTP `000`,
and `http://localhost:3000` refused connection. The Colima logs show the VM had
been started and stopped earlier on `2026-07-22` (`10:31:18+09:00` to
`10:41:27+09:00`, then `10:42:39+09:00` to `10:44:58+09:00`), with LabTether
port forwarding including `3000` and `28443` torn down before this checkpoint.
Colima was not started for this checkpoint because that would convert the
continuous-soak question into a restart/recovery event.

Because the installed runtime and console were unavailable, this checkpoint
still cannot confirm the three `labtetherqar17` application containers used
image `sha256:7f088d78bfbb26b6a350096110fce2105ba6cd022192f15cc3d6534e5d0e0940`,
were healthy, had `RestartCount=0`, had `OOMKilled=false`, or had no fatal,
panic, lease-loss, incomplete-drain, closed-pool, or shutdown-error signature
since `2026-07-16T16:15:00Z`. It also cannot confirm the preserved PostgreSQL
container, strict `/version` and `/healthz`, or current disposable Home
Assistant console inventory. No production Home Assistant connector,
credential, or household automation was changed.

## r27 automation current-system checkpoint - 2026-07-23

Checkpoint result: **FAIL / blocker for the exact-r27 24-hour installed soak;
current runtime is a different candidate and Home Assistant is not currently
Online**.

This read-only automation checkpoint ran at `2026-07-23T02:21:28Z`. Docker was
reachable through context `colima`, but the live `labtetherqar17` application
containers no longer used r27 image
`sha256:7f088d78bfbb26b6a350096110fce2105ba6cd022192f15cc3d6534e5d0e0940`.
Hub, web console, and console ingress instead used
`labtether-installed-qa:20260723-r31-dockerfix-83695f01` /
`sha256:384b9f3f21afb8072b1091d634380d5cbafac07b2e5e4e770b7f768b38608ee1`,
with fresh `2026-07-23T02:13Z`-`02:15Z` start times. They were healthy,
restart-zero, and `OOMKilled=false`; a current-log scan found no requested
fatal, panic, lease-loss, incomplete-drain, closed-pool, or shutdown-error
strings, but this is r31-dockerfix evidence and cannot prove the r27 soak
window.

PostgreSQL retained container
`d4fd5c5478ef59cf99b7d0923e3c6da2b358dbdaf63a6d49fba8af74ebb038f3`, start
time `2026-07-22T09:25:33.948207088Z`, restart count zero, healthy state,
`OOMKilled=false`, and the preserved QA database (`96/96` migrations, one user,
24 assets, three collectors, and two sessions). Strict private-CA `/version`
returned `20260723-r31-dockerfix-83695f01`, not `20260717-r27`; `/healthz`
reported PostgreSQL `ok`.

The rendered installed console was reachable only at the unauthenticated login
screen during this run because the retained r28 cookie was stale (`401`), and
no credential or session state was mutated to force access. Read-only
PostgreSQL and collector readback showed the disposable Home Assistant root
`Disposable HA QA r17` as **Offline**. Its collector last ran at
`2026-07-23T02:22:12.213025Z` and failed with connection refused to
`https://host.docker.internal:18444/api/states`. The 17 retained Home Assistant
entity children remained visible in storage as stale inventory: 10 online and
7 offline entity rows. That is not current Home Assistant root Online proof.

No production Home Assistant connector, credential, household automation,
backup job, recovery unit, prohibited VM, or Windows one-time token was
touched. The 13 credential rotations, unsigned Windows release, and Delta VM
100, Delta VM 101, and Gamma VM 100 recovery proofs remain open; the earlier
config-only backup-job exclusion is the only superseded residual from the
original r27 checkpoint notes.

## r27 automation current-system checkpoint - 2026-07-24

Checkpoint result: **FAIL / blocker for the exact-r27 24-hour installed soak;
current runtime is again a different candidate and Home Assistant is not
currently Online**.

This read-only automation checkpoint ran at `2026-07-24T02:27:51Z`. Docker was
reachable through context `colima`, but the live `labtetherqar17` application
containers no longer used r27 image
`sha256:7f088d78bfbb26b6a350096110fce2105ba6cd022192f15cc3d6534e5d0e0940`.
Hub, web console, and console ingress instead used
`labtether-installed-qa:20260723-main-1a4290af` /
`sha256:a4bcfa50ef25967af4759471ebe1ed2ab6ec79b2d711c7e00547092ab4ddd307`,
with fresh `2026-07-23T04:58:49Z`, `2026-07-23T04:58:54Z`, and
`2026-07-23T04:59:00Z` start times. They were healthy, restart-zero, and
`OOMKilled=false`; a current-log scan since `2026-07-16T16:15:00Z` found no
requested fatal, panic, lease-loss, incomplete-drain, closed-pool, OOM, or
shutdown-error strings, but this is main-candidate evidence and cannot prove
the r27 soak window.

PostgreSQL retained container
`d4fd5c5478ef59cf99b7d0923e3c6da2b358dbdaf63a6d49fba8af74ebb038f3`, start
time `2026-07-22T09:25:33.948207088Z`, restart count zero, healthy state,
`OOMKilled=false`, and the preserved QA database (`96/96` migrations, one user,
26 assets, two hub collectors, and zero active sessions). Strict private-CA
`/version` returned `20260723-main-1a4290af`, not `20260717-r27`; `/healthz`
reported PostgreSQL `ok`.

The installed console rendered only the unauthenticated login screen; protected
console API calls returned `401`, and no credential or session state was
mutated to force access. Read-only PostgreSQL and collector readback showed the
disposable Home Assistant root `Disposable HA QA r17` as **Offline**. Its
collector last ran at `2026-07-24T02:26:39.020622Z` and failed with connection
refused to `https://host.docker.internal:18444/api/states`. The 17 retained
Home Assistant entity children remained visible in storage as stale inventory:
10 online and 7 offline entity rows. That is not current Home Assistant root
Online proof.

No production Home Assistant connector, credential, household automation,
backup job, recovery unit, prohibited VM, or Windows one-time token was
touched. The 13 credential rotations, unsigned Windows release, and Delta VM
100, Delta VM 101, and Gamma VM 100 recovery proofs remain open; the earlier
config-only backup-job exclusion remains the only superseded residual from the
original r27 checkpoint notes.
