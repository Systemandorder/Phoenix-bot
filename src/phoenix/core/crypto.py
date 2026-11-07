"""Персональні ключі шифрування користувачів (за бажанням — «загорнуті» MASTER_KEY).

Старий бот зберігає Fernet-ключі у bot_settings відкритим текстом.
Загорнутий формат: 'enc1:<токен>'. Читання підтримує обидва формати;
перехід на загорнутий — лише коли wrap=True
(вмикати ТІЛЬКИ після повного переходу: старий бот загорнуті ключі не прочитає).
Fernet = AES-128-CBC + HMAC-SHA256 (з перевіркою цілісності).
"""
from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from .kv import KV, Keys

_WRAP_PREFIX = "enc1:"


class KeyVaultError(RuntimeError):
    """Не вдалося отримати ключ або розшифрувати дані."""


class KeyVault:
    def __init__(self, kv: KV, master_key: str, wrap: bool = False) -> None:
        self._kv = kv
        self._master = Fernet(master_key.encode())  # ValueError, якщо MASTER_KEY некоректний
        self._wrap = wrap

    def user_key(self, user_id: int) -> bytes:
        name = Keys.crypto(user_id)
        stored = self._kv.get(name)
        if stored is None:
            fresh = Fernet.generate_key()
            self._kv.set_if_absent(name, self._pack(fresh))  # безпечно при гонці
            stored = self._kv.get(name)  # беремо те, що реально записано
        if stored is None:
            raise KeyVaultError(f"Не вдалося створити ключ для користувача {user_id}")
        if stored.startswith(_WRAP_PREFIX):
            try:
                return self._master.decrypt(stored[len(_WRAP_PREFIX):].encode())
            except InvalidToken as exc:
                raise KeyVaultError(
                    "Ключ користувача не розшифровується: MASTER_KEY змінено або дані пошкоджено"
                ) from exc
        key = stored.encode()
        if self._wrap:  # ліниве оновлення старого відкритого ключа
            self._kv.set(name, self._pack(key))
        return key

    def encrypt(self, user_id: int, data: bytes) -> bytes:
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("encrypt очікує bytes (для тексту: s.encode('utf-8'))")
        return Fernet(self.user_key(user_id)).encrypt(bytes(data))

    def decrypt(self, user_id: int, token: bytes) -> bytes:
        try:
            return Fernet(self.user_key(user_id)).decrypt(token)
        except InvalidToken as exc:
            raise KeyVaultError("Дані не розшифровуються: хибний ключ або файл пошкоджено") from exc

    def _pack(self, key: bytes) -> str:
        if self._wrap:
            return _WRAP_PREFIX + self._master.encrypt(key).decode()
        return key.decode()
