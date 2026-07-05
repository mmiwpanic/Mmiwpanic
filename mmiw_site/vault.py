from __future__ import annotations
import secrets
from typing import Optional, Tuple
from .settings import settings

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    CRYPTO_OK = True
except Exception:
    CRYPTO_OK = False


def vault_enabled() -> bool:
    """True only if both the crypto library is present AND a valid key is
    configured. Callers should check this explicitly rather than assuming
    encrypt_bytes always encrypts — see the NOTE in encrypt_bytes below."""
    return CRYPTO_OK and _key() is not None


def _key() -> Optional[bytes]:
    k = settings.vault_key
    if not k:
        return None
    try:
        if all(c in "0123456789abcdef" for c in k.lower()) and len(k) in (32, 48, 64):
            key_bytes = bytes.fromhex(k)
        else:
            key_bytes = k.encode()
    except Exception:
        return None
    if len(key_bytes) not in (16, 24, 32):
        return None
    return key_bytes


def encrypt_bytes(plaintext: bytes) -> Tuple[bytes, Optional[bytes]]:
    """Returns (ciphertext_or_plaintext, nonce_or_None).
    IMPORTANT: if vault_enabled() is False, this returns the plaintext
    UNCHANGED with nonce=None. Callers MUST check vault_enabled() (or check
    whether nonce is None) before treating the output as encrypted — this
    function does not raise or warn on its own, by design, so a missing key
    never blocks an upload. It's the caller's job to know whether encryption
    actually happened, e.g. to store that fact alongside the file record.
    AES-GCM's authentication tag is appended to the ciphertext automatically
    by the `cryptography` library — there is no separate tag value to track.
    """
    key = _key()
    if not (CRYPTO_OK and key):
        return plaintext, None
    nonce = secrets.token_bytes(12)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, None)
    return ciphertext, nonce


def decrypt_bytes(ciphertext: bytes, nonce: Optional[bytes]) -> bytes:
    """Reverses encrypt_bytes. If nonce is None, assumes the data was never
    encrypted (matches encrypt_bytes' behavior when vault was disabled at
    write time) and returns it unchanged. Raises if the key is missing but a
    nonce IS present — that means we have encrypted data we can no longer
    read, which the caller needs to know about rather than get back garbage
    bytes silently."""
    if nonce is None:
        return ciphertext
    key = _key()
    if not (CRYPTO_OK and key):
        raise RuntimeError(
            "Cannot decrypt: this file was encrypted but MMIW_VAULT_KEY is "
            "missing or invalid in the current environment."
        )
    return AESGCM(key).decrypt(nonce, ciphertext, None)
