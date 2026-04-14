ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base:3.19
ARG HUB_IMAGE=ghcr.io/labtether/labtether:latest

FROM ${HUB_IMAGE} AS hub

FROM ${BUILD_FROM}

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN apk add --no-cache \
  bash \
  ca-certificates \
  jq \
  postgresql16 \
  postgresql16-client \
  su-exec

COPY --from=hub /usr/local/bin/labtether /usr/local/bin/labtether
COPY addon/labtether/run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
