"""Encryption of stored secrets (CalDAV passwords) with a key from the environment.

Only a person who holds ``EMILE_SECRET_KEY`` can read them back: a copy of the database alone is not enough. The
Fernet key is derived from the secret, so any string of 24 characters or more works.
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken


class VaultError(Exception):
    """The secret cannot be decrypted (``EMILE_SECRET_KEY`` changed since it was stored)."""


class Vault:
    def __init__(self, secret_key: str) -> None:
        key = hashlib.pbkdf2_hmac("sha256", secret_key.encode(), b"emile-vault-v1", 100_000)
        self._fernet = Fernet(base64.urlsafe_b64encode(key))

    def encrypt(self, plain: str) -> str:
        return self._fernet.encrypt(plain.encode()).decode()

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken:
            raise VaultError("the stored password cannot be decrypted: EMILE_SECRET_KEY changed") from None
