"""Plan signing: domain-separated HMAC-SHA256 bootstrap (S13 SIGNING, P16).

The signed message is the domain tag ``FOCUS_PLAN_V1\\0`` followed by the canonical JSON of
``{key_id, scope, purpose, source_sha, policy_sha, payload_sha}``, which is unambiguous. The key
id is tracked separately from the key.

HMAC is symmetric: whoever can verify can also sign, so verify-only custody is never claimed.

Key material comes from the settings reference ``FOCUS_SIGN_KEY_REF``, either ``env:NAME`` or
``vcap:service/field``, with ``FOCUS_SIGN_KEY_ID``. It is never logged, returned, stored in a
receipt or sent to a client.

Fixture mode without a configured key uses a published, deterministic TEST key with
``test_key = True``. That key can rehearse the protocol but can never produce a live acceptance.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
from dataclasses import dataclass, field
from typing import Any, Mapping

from ..codec import canonical_dumps

DOMAIN = b"FOCUS_PLAN_V1\x00"
FIXTURE_KEY_ID = "fixture-test-key-v1"
_FIXTURE_LABEL = b"FOCUS fixture TEST signing key: public, deterministic, never valid for live acceptance"


@dataclass(frozen=True)
class Signer:
    key_id: str
    test_key: bool
    _key: bytes = field(repr=False)

    def __repr__(self) -> str:                    # never print key material
        return f"Signer(key_id={self.key_id!r}, test_key={self.test_key})"

    @staticmethod
    def message(key_id: str, scope: str, purpose: str, source_sha: str, policy_sha: str, payload_sha: str) -> bytes:
        return DOMAIN + canonical_dumps({"key_id": key_id, "scope": scope, "purpose": purpose, "source_sha": source_sha, "policy_sha": policy_sha,
                                         "payload_sha": payload_sha})

    def sign(self, scope: str, purpose: str, source_sha: str, policy_sha: str, payload_sha: str) -> str:
        return hmac.new(self._key, self.message(self.key_id, scope, purpose, source_sha, policy_sha, payload_sha), hashlib.sha256).hexdigest()

    def verify(self, signature_hex: str, key_id: str, scope: str, purpose: str, source_sha: str, policy_sha: str, payload_sha: str) -> bool:
        if key_id != self.key_id or not isinstance(signature_hex, str):
            return False
        expected = self.sign(scope, purpose, source_sha, policy_sha, payload_sha)
        return hmac.compare_digest(expected, signature_hex)


def fixture_test_signer() -> Signer:
    return Signer(FIXTURE_KEY_ID, True, hashlib.sha256(_FIXTURE_LABEL).digest())


def _decode_key(material: str) -> bytes | None:
    text = material.strip()
    for decoder in (bytes.fromhex, lambda s: base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))):
        try:
            key = decoder(text)
        except (ValueError, binascii.Error):
            continue
        if len(key) >= 32:
            return key
    return None


def _vcap_field(environ: Mapping[str, str], ref: str) -> str | None:
    service, _, field = ref.partition("/")
    try:
        services = json.loads(environ.get("VCAP_SERVICES", "{}"))
    except ValueError:
        return None
    for entries in services.values() if isinstance(services, dict) else ():
        for entry in entries if isinstance(entries, list) else ():
            if isinstance(entry, dict) and service in (entry.get("name"), entry.get("instance_name")):
                value = (entry.get("credentials") or {}).get(field)
                return str(value) if value else None
    return None


def secret_from_ref(ref: str, environ: Mapping[str, str] | None = None) -> tuple[str | None, bool]:
    """(material, supported) for a protected reference ``env:NAME`` or ``vcap:service/field``.

    The material is for the caller's own use only: never logged, returned to a client or stored.
    """
    environ = os.environ if environ is None else environ
    if ref.startswith("env:"):
        return environ.get(ref[4:]) or None, True
    if ref.startswith("vcap:"):
        return _vcap_field(environ, ref[5:]), True
    return None, False


def resolve_signer(settings: Any, environ: Mapping[str, str] | None = None) -> tuple[Signer | None, str]:
    """(signer, reason). Live mode never falls back to the TEST key."""
    environ = os.environ if environ is None else environ
    ref = getattr(settings, "sign_key_ref", None)
    if ref:
        material, supported = secret_from_ref(ref, environ)
        if not supported:
            return None, "SIGN_KEY_REF_UNSUPPORTED: use env:NAME or vcap:service/field"
        key = _decode_key(material) if material else None
        if key is None:
            return None, "SIGN_KEY_UNAVAILABLE: referenced key material is missing or shorter than 32 bytes"
        key_id = getattr(settings, "sign_key_id", None)
        if not key_id:
            return None, "SIGN_KEY_ID_MISSING: FOCUS_SIGN_KEY_ID must name the key separately"
        return Signer(key_id, False, key), "CONFIGURED"
    if getattr(settings, "mode", None) == "fixture":
        return fixture_test_signer(), "FIXTURE_TEST_KEY"
    return None, "SIGN_KEY_ABSENT: live signing needs FOCUS_SIGN_KEY_REF and FOCUS_SIGN_KEY_ID"
