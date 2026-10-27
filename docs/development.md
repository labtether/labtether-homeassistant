# Development and releases

This is for contributors. To install the integration, see the [README](../README.md).

## Link into a Home Assistant config

```bash
ln -s /path/to/labtether-homeassistant/custom_components/labtether \
      /path/to/ha-config/custom_components/labtether
```

## Tests and QA

```bash
python -m pip install --require-hashes -r requirements-dev.txt
python -m pytest tests/ -v
./tests/addon_container_security_test.sh  # requires Docker
LABTETHER_LIVE_HA_QA=1 ./tests/ha_core_live_test.sh  # disposable HA Core lifecycle
./tests/ha_core_cross_product_instance.sh start     # disposable hub-connector target
```

The cross-product instance preserves its HTTP route and also creates an HTTPS
route for connector TLS testing. It writes its seven-day QA token to
`/tmp/labtether-ha-cross-qa-token` and its disposable CA certificate to
`/tmp/labtether-ha-cross-tls/ca.pem`, both with mode `0600`. Docker-hosted hubs
can use `https://host.docker.internal:18444`; untrusted TLS fails closed, while
the connector's explicit `skip_verify` option can be exercised for this isolated
self-signed target. Use `status`, `restart`, or `stop` to manage the whole
instance, or `tls-start`, `tls-restart`, and `tls-stop` to manage only the HTTPS
route. Never reuse its disposable credentials or TLS bypass outside QA.
Each TLS restart generates a fresh certificate chain, so CA-trusting clients
must reread the PEM from the same path afterward.

To verify the exact candidate hub connector path without printing either token:

```bash
LABTETHER_QA_HUB_CONTAINER=<hub-container> \
LABTETHER_QA_HUB_URL=https://<hub-host>:<port> \
LABTETHER_QA_HUB_CA_FILE=</path/to/candidate-hub-ca.pem> \
  ./tests/verify_ha_cross_tls_connector.sh
```

Omit `LABTETHER_QA_HUB_CA_FILE` only when the candidate Hub certificate is
already trusted by the system. The verifier never disables candidate Hub
certificate or hostname checks.

## Releasing

Tag a release only after the matching Hub release is published: the add-on
release workflow builds from `ghcr.io/labtether/labtether:<tag>`. The tag must
look like `v1.7.0` and match the `version` in
`custom_components/labtether/manifest.json` and `addon/labtether/config.json`;
the workflow stops before building anything otherwise.
