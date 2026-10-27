# hadolint global ignore=DL3002
ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base:3.24@sha256:20d9028d56eaa3dd3013f6e0a7957b4911e4c71e14f85d1f609bba4fe429e84a
# `scratch` is an intentionally non-runnable sentinel. Release and local builds
# must provide a versioned, registry-resolved digest through
# --build-arg HUB_IMAGE=...@sha256:; the validator rejects this default.
ARG HUB_IMAGE=scratch

# Fail closed when a caller tries to substitute a mutable base or hub image.
# A tag may be retained for readability, but the registry digest is mandatory.
FROM ${BUILD_FROM} AS reference-validator
ARG BUILD_FROM
ARG HUB_IMAGE
SHELL ["/bin/ash", "-eo", "pipefail", "-c"]
RUN printf '%s\n%s\n' "${BUILD_FROM}" "${HUB_IMAGE}" \
  | grep -Eq '^.+@sha256:[0-9a-f]{64}$' \
  && test "$(printf '%s\n%s\n' "${BUILD_FROM}" "${HUB_IMAGE}" \
    | grep -Ec '^.+@sha256:[0-9a-f]{64}$')" -eq 2 \
  && touch /immutable-inputs-verified

FROM ${HUB_IMAGE} AS hub

FROM ${BUILD_FROM}

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

COPY --from=reference-validator /immutable-inputs-verified /immutable-inputs-verified

# Keep PostgreSQL 16 data directories usable; a major upgrade needs an explicit
# pg_upgrade or dump/restore path before changing the persisted database format.
RUN apk add --update-cache --no-cache \
  bash=5.3.9-r1 \
  c-ares=1.34.9-r0 \
  ca-certificates=20261020-r0 \
  jq=1.8.2-r0 \
  libcrypto3=3.5.9-r0 \
  libssl3=3.5.9-r0 \
  libstdc++=15.2.0-r5 \
  postgresql16=16.15-r0 \
  postgresql16-client=16.15-r0 \
  su-exec=0.3-r0 \
  && addgroup -S -g 10001 labtether \
  && adduser -S -D -H -u 10001 -G labtether -h /home/labtether -s /sbin/nologin labtether \
  && install -d -m 0700 -o labtether -g labtether /home/labtether \
  && rm -f /usr/bin/tempio \
  && rm /immutable-inputs-verified

COPY --from=hub /usr/local/bin/labtether /usr/local/bin/labtether
# The web console holds the owner setup screen. Both images use Alpine 3.24, so
# the Hub image's Node binary runs here with only libstdc++ added above. Code
# stays root-owned; the console may write only its Next.js cache.
COPY --from=hub /usr/local/bin/node /usr/local/bin/node
COPY --from=hub --chown=root:root /app/console/ /app/console/
# Agent installers are baked into the Hub release image; the Hub no longer
# downloads them at runtime.
COPY --from=hub --chown=root:root /opt/labtether/agents/ /opt/labtether/agents/
COPY addon/labtether/run.sh /run.sh
COPY addon/labtether/lib/ /usr/local/lib/labtether-addon/
COPY addon/labtether/rootfs/ /
RUN chmod +x /run.sh \
    /etc/s6-overlay/s6-rc.d/console/run \
    /etc/s6-overlay/s6-rc.d/console-front/run \
  && install -d -m 0700 -o labtether -g labtether /app/console/.next/cache \
  && /usr/local/bin/node --version >/dev/null \
  && test -f /app/console/server.js

# The entrypoint needs root only to prepare mounted volumes and start the local
# postgres sidecar. run.sh drops to UID/GID 10001 before it execs the hub.
# nosemgrep: dockerfile.security.last-user-is-root.last-user-is-root -- root is the HA supervisor only; run.sh executes the network-facing hub as UID/GID 10001.
USER root

# s6 clears Docker/Supervisor environment variables before launching CMD.
# Restore them so external TLS paths and runtime limits reach the bootstrap.
CMD ["/command/with-contenv", "/run.sh"]
