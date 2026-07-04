from __future__ import annotations
import hashlib, os
from pathlib import Path
from typing import Optional, Tuple
from . import vault

UPLOAD_DIR = Path(os.environ.get("MMIW_UPLOAD_DIR", "uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def save_and_hash(filename: str, data: bytes) -> Tuple[str, str, bool, Optional[str]]:
    """Saves a file to disk, encrypting it at rest if MMIW_VAULT_KEY is
    configured. Returns (stored_path, sha256_of_plaintext, was_encrypted, nonce_hex).

    IMPORTANT: the sha256 returned is always the hash of the ORIGINAL
    plaintext content, never the ciphertext. This is deliberate — evidence
    integrity verification (evidence_locker-style chain of custody) needs to
    confirm the real file content hasn't changed, which means hashing before
    encryption. Hashing the ciphertext instead would make the hash useless
    for that purpose, since the same plaintext encrypted twice produces two
    different ciphertexts (a fresh random nonce each time).
    """
    plaintext_hash = hashlib.sha256(data).hexdigest()
    out_dir = UPLOAD_DIR / plaintext_hash[:2] / plaintext_hash[2:4]
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / filename

    ciphertext, nonce = vault.encrypt_bytes(data)
    was_encrypted = nonce is not None
    path.write_bytes(ciphertext)

    nonce_hex = nonce.hex() if nonce else None
    return str(path), plaintext_hash, was_encrypted, nonce_hex


def load_and_decrypt(stored_path: str, encrypted: bool, nonce_hex: Optional[str]) -> bytes:
    """Reads a stored file back, decrypting it if it was encrypted, and
    returns the plaintext bytes. Raises if the file was encrypted but the
    key is no longer available (via vault.decrypt_bytes) rather than
    returning ciphertext disguised as plaintext."""
    with open(stored_path, "rb") as f:
        raw = f.read()
    if encrypted:
        nonce = bytes.fromhex(nonce_hex) if nonce_hex else None
        plaintext = vault.decrypt_bytes(raw, nonce)
    else:
        plaintext = raw
    return plaintext


def scrub_exif_if_image(data: bytes) -> bytes:
    return data

