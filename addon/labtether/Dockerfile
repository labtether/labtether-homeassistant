# hadolint global ignore=DL3002
ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base:3.23@sha256:e976b27157be0f89fd5bd4757ec6377d2576963623deb135246d0f3c5742f462
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

RUN apk add --no-cache \
  bash=5.3.3-r1 \
  c-ares=1.34.8-r0 \
  ca-certificates=20260611-r0 \
  jq=1.8.1-r0 \
  libcrypto3=3.5.7-r0 \
  libssl3=3.5.7-r0 \
  postgresql16=16.14-r0 \
  postgresql16-client=16.14-r0 \
  su-exec=0.3-r0 \
  && addgroup -S -g 10001 labtether \
  && adduser -S -D -H -u 10001 -G labtether -h /home/labtether -s /sbin/nologin labtether \
  && install -d -m 0700 -o labtether -g labtether /home/labtether \
  && rm -f /usr/bin/tempio \
  && rm /immutable-inputs-verified

COPY --from=hub /usr/local/bin/labtether /usr/local/bin/labtether
COPY addon/labtether/run.sh /run.sh
RUN chmod +x /run.sh

# The entrypoint needs root only to prepare mounted volumes and start the local
# postgres sidecar. run.sh drops to UID/GID 10001 before it execs the hub.
# nosemgrep: dockerfile.security.last-user-is-root.last-user-is-root -- root is the HA supervisor only; run.sh executes the network-facing hub as UID/GID 10001.
USER root

CMD ["/run.sh"]
