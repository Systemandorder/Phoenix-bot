"""Per-user encryption keys, optionally wrapped by MASTER_KEY.

Legacy stores plaintext Fernet keys in bot_settings. Wrapped format: 'enc1:<token>'.
Reading supports both; upgrade to wrapped happens only when wrap=True
(enable AFTER cutover - the old bot cannot read wrapped keys).
Fernet = AES-128-CBC + HMAC-SHA256 (authenticated).
"""
from __future__ import annotations

from cryptography.fernet import Fernet

from .kv import KV, Keys

_WRAP_PREFIX = "enc1:"


class KeyVault:
    def __init__(self, kv: KV, master_key: str, wrap: bool = False) -> None:
        self._kv = kv
        self._master = Fernet(master_key.encode())  # ValueError if MASTER_KEY is invalid
        self._wrap = wrap

    def user_key(self, user_id: int) -> bytes:
        name = Keys.crypto(user_id)
        stored = self._kv.get(name)
        if stored is None:
            fresh = Fernet.generate_key()
            self._kv.set_if_absent(name, self._pack(fresh))  # race-safe
            stored = self._kv.get(name)
        if stored.startswith(_WRAP_PREFIX):
            return self._master.decrypt(stored[len(_WRAP_PREFIX):].encode())
        key = stored.encode()
        if self._wrap:  # lazy upgrade of legacy plaintext key
            self._kv.set(name, self._pack(key))
        return key

    def encrypt(self, user_id: int, data: bytes) -> bytes:
        return Fernet(self.user_key(user_id)).encrypt(data)

    def decrypt(self, user_id: int, token: bytes) -> bytes:
        return Fernet(self.user_key(user_id)).decrypt(token)

    def _pack(self, key: bytes) -> str:
        if self._wrap:
            return _WRAP_PREFIX + self._master.encrypt(key).decode()
        return key.decode()
