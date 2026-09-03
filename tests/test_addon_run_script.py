"""Tests for the Home Assistant add-on entrypoint helpers."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path


RUN_SCRIPT = Path(__file__).parents[1] / "addon" / "labtether" / "run.sh"
ADDON_CONFIG = Path(__file__).parents[1] / "addon" / "labtether" / "config.json"
DOCKERFILE = Path(__file__).parents[1] / "addon" / "labtether" / "Dockerfile"
CI_WORKFLOWS = tuple((Path(__file__).parents[1] / ".github" / "workflows").glob("*.yml"))
DEV_REQUIREMENTS = Path(__file__).parents[1] / "requirements-dev.txt"
DEPENDABOT_CONFIG = Path(__file__).parents[1] / ".github" / "dependabot.yml"
TLS_CONNECTOR_SCRIPT = Path(__file__).parent / "verify_ha_cross_tls_connector.sh"


def test_run_script_has_valid_bash_syntax():
    subprocess.run(["bash", "-n", RUN_SCRIPT], check=True)


def test_setup_token_is_one_time_file_backed_secret():
    """A consumed setup token must not be restored from persistent env state."""
    run_script = RUN_SCRIPT.read_text()

    assert 'persist_state_value "LABTETHER_SETUP_TOKEN"' not in run_script
    assert "setup-token-option.sha256" in run_script
    assert "setup-token-issued.sha256" in run_script
    assert 'export LABTETHER_SETUP_TOKEN_FILE="${SETUP_TOKEN_FILE}"' in run_script
    assert 'unset LABTETHER_SETUP_TOKEN_FILE LABTETHER_SETUP_TOKEN' in run_script
    assert 'first-run setup token: ${LABTETHER_SETUP_TOKEN}' not in run_script
    assert 'token values are never written to logs' in run_script


def test_hub_drops_to_dedicated_nonroot_user_with_scoped_writable_paths():
    run_script = RUN_SCRIPT.read_text()
    dockerfile = DOCKERFILE.read_text()

    assert "adduser -S -D -H -u 10001" in dockerfile
    assert 'exec su-exec "${HUB_USER}:${HUB_GROUP}" /usr/local/bin/labtether' in run_script
    assert 'chown root:root /data' in run_script
    assert 'chmod 0711 /data' in run_script
    assert 'chown -RhP postgres:postgres "${pgdata}"' in run_script
    assert 'readonly ROOT_STATE_DIR="/data/labtether-addon-root"' in run_script
    assert 'chown -RhP root:root "${path}"' in run_script
    for path in ("/data/install", "/data/certs", "/data/agents", "/data/recordings", "/run/labtether", "/ca"):
        assert path in run_script


def test_root_bootstrap_never_sources_hub_writable_state():
    """Legacy shell state may run only after dropping to the Hub user."""
    run_script = RUN_SCRIPT.read_text()

    assert 'readonly STATE_JSON_FILE="${ROOT_STATE_DIR}/runtime.json"' in run_script
    assert "migrate_legacy_setup_markers" in run_script
    assert "LEGACY_SETUP_TOKEN_ISSUED_MARKER_FILE" in run_script
    assert 'su-exec "${HUB_USER}:${HUB_GROUP}" /usr/bin/env -i' in run_script
    assert 'source "$1"' in run_script
    assert 'source "${LEGACY_STATE_ENV_FILE}"' not in run_script
    assert 'source "${STATE_JSON_FILE}"' not in run_script
    assert "state_json_is_valid" in run_script


def test_container_build_inputs_are_digest_pinned_and_validated():
    dockerfile = DOCKERFILE.read_text()
    base_arg_pattern = re.compile(r"^ARG BUILD_FROM=.+@sha256:[0-9a-f]{64}$", re.MULTILINE)

    assert len(base_arg_pattern.findall(dockerfile)) == 1
    assert re.search(r"^ARG HUB_IMAGE=scratch$", dockerfile, re.MULTILINE)
    assert "intentionally non-runnable sentinel" in dockerfile
    assert "AS reference-validator" in dockerfile
    assert "COPY --from=reference-validator /immutable-inputs-verified" in dockerfile


def test_ci_actions_and_python_test_dependencies_are_immutable():
    action_pattern = re.compile(r"^\s*uses:\s*[^\s]+@([^\s#]+)", re.MULTILINE)
    for workflow in CI_WORKFLOWS:
        refs = action_pattern.findall(workflow.read_text())
        assert refs, f"expected actions in {workflow}"
        assert all(re.fullmatch(r"[0-9a-f]{40}", ref) for ref in refs)

    requirements = DEV_REQUIREMENTS.read_text()
    assert "--hash=sha256:" in requirements
    assert "homeassistant==" not in requirements
    assert "aiohttp==3.14.3" in requirements
    assert "pytest==9.1.1" in requirements
    assert "pytest-asyncio==1.4.0" in requirements
    assert "voluptuous==0.16.0" in requirements


def test_dependabot_covers_all_supply_chain_ecosystems_with_cooldown():
    config = DEPENDABOT_CONFIG.read_text()

    for ecosystem in ("pip", "docker", "github-actions"):
        assert f"package-ecosystem: {ecosystem}" in config
    assert config.count("default-days: 7") == 3


def test_blank_admin_password_selects_setup_flow_instead_of_generated_password():
    run_script = RUN_SCRIPT.read_text()

    assert 'require_or_generate "LABTETHER_ADMIN_PASSWORD"' not in run_script
    assert 'LABTETHER_ADMIN_PASSWORD="${ADMIN_PASSWORD_OPT:-${PROCESS_ADMIN_PASSWORD}}"' in run_script
    assert 'persist_state_value "LABTETHER_ADMIN_PASSWORD"' not in run_script


def test_addon_publishes_https_only_by_default():
    config = json.loads(ADDON_CONFIG.read_text())

    assert config["ports"]["8080/tcp"] is None
    assert config["ports"]["8443/tcp"] == 8443


def test_cross_tls_verifier_rejects_http_before_reading_tokens():
    """The credential recipient must be authenticated before secret reads."""
    result = subprocess.run(
        ["bash", TLS_CONNECTOR_SCRIPT],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "LABTETHER_QA_HUB_CONTAINER": "unused",
            "LABTETHER_QA_HUB_URL": "http://127.0.0.1:8080",
        },
    )

    assert result.returncode != 0
    assert "must use verified HTTPS" in result.stderr


def test_cross_tls_verifier_never_disables_candidate_hub_verification():
    """The outer Hub request must use system trust or an explicit CA."""
    verifier = TLS_CONNECTOR_SCRIPT.read_text()

    assert "curl -k" not in verifier
    assert "curl -ksS" not in verifier
    assert "--cacert" in verifier
    assert "--noproxy '*'" in verifier
    assert '.service == "labtether-hub"' in verifier
    assert 'docker exec "${HUB_CONTAINER}" cat -- "${HUB_TOKEN_PATH}"' in verifier
    assert verifier.index('"${HUB_URL%/}/"') < verifier.index('hub_token="$(')
