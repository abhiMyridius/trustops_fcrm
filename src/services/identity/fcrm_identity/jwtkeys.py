"""The one RS256 signing keypair X1 holds (design A §2.6, §8.1).

Fix carried from the cloned ``evoqidentityservice`` (CLAUDE.md's AEO-reuse
table, bug 3): ``config.py`` there gave ``JWT_SECRET_KEY`` a hard-coded
32-byte hex fallback (an HS256 secret baked into source control) used
silently whenever the environment variable was unset. Here there is no
fallback: :func:`SigningKey.from_file` raises :class:`SigningKeyError`
when ``JWT_KEY_FILE`` is missing, unreadable or not an RSA private key,
and ``lifespan.py`` lets that exception stop startup rather than catching
it and minting an ephemeral or default key.

Key rotation (the cloned service's ``signing_keys`` Mongo collection,
design A §2.6) is pruned: one keypair, loaded once at startup, is enough
for the demo's lifetime, and ``UPSTREAM.md`` records this as a scope
decision, not an oversight.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey


class SigningKeyError(RuntimeError):
    """``JWT_KEY_FILE`` does not resolve to a usable RSA private key."""


class SigningKey:
    """One loaded RSA keypair plus the stable ``kid`` derived from it.

    Args:
        private_key: The RSA private key used to sign tokens.

    The ``kid`` is the first 16 hex characters of the SHA-256 digest of
    the public key's DER encoding, so it is stable across restarts
    without a database round trip, and changes automatically if the key
    file is ever rotated.
    """

    def __init__(self, private_key: RSAPrivateKey) -> None:
        self.private_key = private_key
        self.public_key: RSAPublicKey = private_key.public_key()
        self.kid = self._derive_kid(self.public_key)

    @staticmethod
    def _derive_kid(public_key: RSAPublicKey) -> str:
        der = public_key.public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        return hashlib.sha256(der).hexdigest()[:16]

    @classmethod
    def from_file(cls, path: str) -> SigningKey:
        """Load the PEM-encoded RSA private key at ``path``.

        Raises:
            SigningKeyError: ``path`` does not exist, is not a readable
                PEM key, or is not an RSA key (RS256 requires RSA; an
                EC or Ed25519 key would silently fail every ``RS256``
                verification later, so it is rejected here instead).
        """
        pem_path = Path(path)
        if not pem_path.is_file():
            raise SigningKeyError(f"JWT_KEY_FILE not found: {path}")
        data = pem_path.read_bytes()
        try:
            key = serialization.load_pem_private_key(data, password=None)
        except (ValueError, TypeError) as exc:
            raise SigningKeyError(
                f"JWT_KEY_FILE is not a valid unencrypted PEM key: {path}"
            ) from exc
        if not isinstance(key, RSAPrivateKey):
            raise SigningKeyError(
                "JWT_KEY_FILE must hold an RSA private key (RS256 requires RSA)"
            )
        return cls(key)
