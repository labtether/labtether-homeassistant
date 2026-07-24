#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
selector="${repo_root}/scripts/ci/select-qa-contracts.sh"
tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/labtether-qa-selector-test.XXXXXX")"
cleanup() {
  rm -rf -- "${tmp_dir}"
}
trap cleanup EXIT

printf '%s\n' "custom_components/labtether/api.py" > "${tmp_dir}/files"
output="$("${selector}" --files-from "${tmp_dir}/files")"
grep -Fq "QA contract ha-disposable-tls:" <<< "${output}"
if grep -Fq "QA contract addon-installed-container:" <<< "${output}"; then
  echo "unrelated add-on contract was selected" >&2
  exit 1
fi

output="$("${selector}" --mode full)"
grep -Fq "QA contract ha-disposable-tls:" <<< "${output}"
grep -Fq "QA contract addon-installed-container:" <<< "${output}"

output="$("${selector}" --base 0000000000000000000000000000000000000000 --head HEAD)"
grep -Fq "QA contract ha-disposable-tls:" <<< "${output}"
grep -Fq "QA contract addon-installed-container:" <<< "${output}"

printf '%s\n' "README.md" > "${tmp_dir}/files"
output="$("${selector}" --files-from "${tmp_dir}/files")"
grep -Fq "No slow QA contracts selected" <<< "${output}"

printf 'broken\trow\n' > "${tmp_dir}/bad-manifest"
if "${selector}" --files-from "${tmp_dir}/files" --manifest "${tmp_dir}/bad-manifest" >/dev/null 2>&1; then
  echo "malformed QA manifest was accepted" >&2
  exit 1
fi

echo "QA contract selector tests passed"
