"""Contracts for anonymous add-on image release checks."""

import io
from email.message import Message
from unittest import TestCase
from unittest.mock import patch

from scripts.release import check_public_image


IMAGE = "ghcr.io/labtether/labtether-homeassistant-addon-amd64:1.2.0"
SCOPE = "repository:labtether/labtether-homeassistant-addon-amd64:pull"
CHALLENGE = f'Bearer realm="https://ghcr.io/token",service="ghcr.io",scope="{SCOPE}"'
DIGEST = "sha256:" + "a" * 64


def headers(**values):
    result = Message()
    for key, value in values.items():
        result[key.replace("_", "-")] = value
    return result


class PublicImageTests(TestCase):
    def test_bearer_challenge_requests_only_anonymous_pull_scope(self):
        response = io.BytesIO(b'{"token":"anonymous-token"}')
        with patch.object(
            check_public_image.urllib.request, "urlopen", return_value=response
        ) as open_url:
            token = check_public_image.anonymous_token(CHALLENGE, SCOPE)
        self.assertEqual(token, "anonymous-token")
        url = open_url.call_args.args[0]
        self.assertTrue(url.startswith("https://ghcr.io/token?"))
        self.assertIn("service=ghcr.io", url)
        self.assertIn(
            "scope=repository%3Alabtether%2Flabtether-homeassistant-addon-amd64%3Apull",
            url,
        )

    def test_public_manifest_must_succeed_after_bearer_challenge(self):
        with patch.object(check_public_image, "head_manifest") as head, patch.object(
            check_public_image, "anonymous_token", return_value="anonymous-token"
        ) as token:
            head.side_effect = [
                (401, headers(WWW_Authenticate=CHALLENGE)),
                (200, headers(Docker_Content_Digest=DIGEST)),
            ]
            self.assertEqual(check_public_image.verify_public_image(IMAGE), DIGEST)
            token.assert_called_once_with(CHALLENGE, SCOPE)
            self.assertEqual(head.call_args_list[-1].args[-1], "anonymous-token")

    def test_private_manifest_fails_after_bearer_challenge(self):
        with patch.object(check_public_image, "head_manifest") as head, patch.object(
            check_public_image, "anonymous_token", return_value="anonymous-token"
        ):
            head.side_effect = [
                (401, headers(WWW_Authenticate=CHALLENGE)),
                (401, headers()),
            ]
            with self.assertRaisesRegex(check_public_image.PublicImageError, "HTTP 401"):
                check_public_image.verify_public_image(IMAGE)

    def test_rejects_untrusted_token_realm(self):
        challenge = CHALLENGE.replace("https://ghcr.io/token", "https://example.org/token")
        with self.assertRaisesRegex(check_public_image.PublicImageError, "token endpoint"):
            check_public_image.anonymous_token(challenge, SCOPE)
