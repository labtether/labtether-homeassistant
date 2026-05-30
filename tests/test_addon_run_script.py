"""Tests for the Home Assistant add-on entrypoint helpers."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


RUN_SCRIPT = Path(__file__).parents[1] / "addon" / "labtether" / "run.sh"


def _extract_shell_function(script: str, name: str) -> str:
    match = re.search(rf"^{name}\(\) \{{\n(?:.*\n)*?^\}}\n", script, re.MULTILINE)
    assert match is not None, f"missing shell function {name}"
    return match.group(0)


def test_generate_password_succeeds_with_pipefail_enabled():
    """Auto-generated admin passwords must not trip bash pipefail."""
    run_script = RUN_SCRIPT.read_text()
    shell = "\n".join(
        [
            "set -Eeuo pipefail",
            _extract_shell_function(run_script, "generate_hex_token"),
            _extract_shell_function(run_script, "generate_password"),
            'password="$(generate_password)"',
            'test "${#password}" -eq 24',
        ]
    )

    subprocess.run(["bash", "-c", shell], check=True)
