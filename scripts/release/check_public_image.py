#!/usr/bin/env python3
"""Verify that GHCR images can be pulled without a GitHub login."""

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request


MANIFEST_TYPES = ", ".join(
    (
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    )
)
IMAGE_REF = re.compile(r"ghcr\.io/([a-z0-9._-]+(?:/[a-z0-9._-]+)+):([A-Za-z0-9_][A-Za-z0-9_.-]*)\Z")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


class PublicImageError(Exception):
    """The image is not available to an anonymous registry client."""


def head_manifest(path: str, tag: str, token: str | None = None):
    headers = {"Accept": MANIFEST_TYPES}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"https://ghcr.io/v2/{path}/manifests/{tag}",
        headers=headers,
        method="HEAD",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, response.headers
    except urllib.error.HTTPError as error:
        with error:
            return error.code, error.headers


def anonymous_token(challenge: str, expected_scope: str) -> str:
    if not challenge.startswith("Bearer "):
        raise PublicImageError("registry did not offer Bearer authentication")
    fields = re.findall(r'(\w+)="([^"]*)"', challenge[7:])
    if len(fields) != 3 or len(dict(fields)) != 3:
        raise PublicImageError("registry Bearer challenge is malformed")
    values = dict(fields)
    realm = urllib.parse.urlsplit(values.get("realm", ""))
    if realm.scheme != "https" or realm.netloc != "ghcr.io" or realm.path != "/token":
        raise PublicImageError("registry token endpoint is unexpected")
    if values.get("service") != "ghcr.io" or values.get("scope") != expected_scope:
        raise PublicImageError("registry token scope is unexpected")
    query = urllib.parse.parse_qsl(realm.query)
    query.extend((key, values[key]) for key in ("service", "scope"))
    token_url = urllib.parse.urlunsplit(
        (realm.scheme, realm.netloc, realm.path, urllib.parse.urlencode(query), "")
    )
    try:
        with urllib.request.urlopen(token_url, timeout=15) as response:
            token = json.load(response).get("token")
    except urllib.error.HTTPError as error:
        raise PublicImageError(
            f"anonymous pull token request returned HTTP {error.code}"
        ) from None
    if not isinstance(token, str) or not token:
        raise PublicImageError("registry did not grant an anonymous pull token")
    return token


def verify_public_image(reference: str) -> str:
    match = IMAGE_REF.fullmatch(reference)
    if match is None:
        raise PublicImageError("expected a tagged ghcr.io image reference")
    path, tag = match.groups()
    status, headers = head_manifest(path, tag)
    if status == 401:
        token = anonymous_token(
            headers.get("WWW-Authenticate", ""), f"repository:{path}:pull"
        )
        status, headers = head_manifest(path, tag, token)
    if status != 200:
        raise PublicImageError(f"anonymous manifest request returned HTTP {status}")
    digest = headers.get("Docker-Content-Digest", "")
    if DIGEST.fullmatch(digest) is None:
        raise PublicImageError("registry returned no valid manifest digest")
    return digest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", action="append", required=True, help="tagged GHCR image")
    args = parser.parse_args()
    failed = False
    for reference in args.image:
        try:
            digest = verify_public_image(reference)
        except (PublicImageError, OSError, ValueError) as error:
            print(f"FAIL {reference}: {error}", file=sys.stderr)
            failed = True
        else:
            print(f"PASS {reference}@{digest}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
