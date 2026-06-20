"""Tests for encryption-at-rest primitives and master-key resolution."""

from __future__ import annotations

import os
import secrets

import pytest

cryptography = pytest.importorskip("cryptography")

from mediarefinery.service.security import (  # noqa: E402
    CIPHERTEXT_FORMAT_VERSION,
    MASTER_KEY_BYTES,
    NONCE_BYTES,
    AesGcmCipher,
    MasterKey,
    MasterKeyError,
    load_or_create_master_key,
    rotate_encrypted_columns,
)
from mediarefinery.service.state_store import StateStore  # noqa: E402


def _key() -> bytes:
    return secrets.token_bytes(MASTER_KEY_BYTES)


def test_encrypt_decrypt_roundtrip():
    """Test encrypt decrypt roundtrip."""
    cipher = AesGcmCipher(_key())
    pt = b"immich-session-token-payload"
    blob = cipher.encrypt(pt)
    assert blob[0] == CIPHERTEXT_FORMAT_VERSION
    assert len(blob) >= 1 + NONCE_BYTES + 16
    assert cipher.decrypt(blob) == pt


def test_encrypt_uses_unique_nonces():
    """Test encrypt uses unique nonces."""
    cipher = AesGcmCipher(_key())
    blobs = {cipher.encrypt(b"same plaintext") for _ in range(50)}
    assert len(blobs) == 50


def test_decrypt_rejects_tampered_ciphertext():
    """Test decrypt rejects tampered ciphertext."""
    cipher = AesGcmCipher(_key())
    blob = bytearray(cipher.encrypt(b"payload"))
    blob[-1] ^= 0xFF
    with pytest.raises(ValueError, match="authentication"):
        cipher.decrypt(bytes(blob))


def test_decrypt_rejects_unknown_version():
    """Test decrypt rejects unknown version."""
    cipher = AesGcmCipher(_key())
    blob = bytearray(cipher.encrypt(b"payload"))
    blob[0] = 0xFE
    with pytest.raises(ValueError, match="version"):
        cipher.decrypt(bytes(blob))


def test_decrypt_rejects_short_blob():
    """Test decrypt rejects short blob."""
    cipher = AesGcmCipher(_key())
    with pytest.raises(ValueError, match="too short"):
        cipher.decrypt(b"\x01\x02\x03")


def test_decrypt_with_wrong_key_fails():
    """Test decrypt with wrong key fails."""
    a = AesGcmCipher(_key())
    b = AesGcmCipher(_key())
    blob = a.encrypt(b"payload")
    with pytest.raises(ValueError, match="authentication"):
        b.decrypt(blob)


def test_associated_data_must_match():
    """Test associated data must match."""
    cipher = AesGcmCipher(_key())
    blob = cipher.encrypt(b"pt", associated_data=b"user-alice")
    assert cipher.decrypt(blob, associated_data=b"user-alice") == b"pt"
    with pytest.raises(ValueError, match="authentication"):
        cipher.decrypt(blob, associated_data=b"user-bob")


def test_cipher_rejects_short_key():
    """Test cipher rejects short key."""
    with pytest.raises(MasterKeyError):
        AesGcmCipher(b"\x00" * 16)


def test_master_key_dataclass_validates_length():
    """Test master key dataclass validates length."""
    with pytest.raises(MasterKeyError):
        MasterKey(key=b"\x00" * 16, source="generated")


def test_load_master_key_from_file(tmp_path):
    """Test load master key from file."""
    raw = secrets.token_bytes(MASTER_KEY_BYTES)
    path = tmp_path / "master.key"
    path.write_bytes(raw)
    mk = load_or_create_master_key(path=path)
    assert mk.key == raw
    assert mk.source == "file"


def test_load_master_key_file_wrong_length(tmp_path):
    """Test load master key file wrong length."""
    path = tmp_path / "master.key"
    path.write_bytes(b"\x00" * 8)
    with pytest.raises(MasterKeyError, match="exactly"):
        load_or_create_master_key(path=path)


def test_load_master_key_generate_when_missing(tmp_path):
    """Test load master key generate when missing."""
    path = tmp_path / "subdir" / "master.key"
    mk = load_or_create_master_key(path=path)
    assert mk.source == "generated"
    assert path.read_bytes() == mk.key
    assert len(mk.key) == MASTER_KEY_BYTES
    if os.name == "posix":
        mode = path.stat().st_mode & 0o777
        assert mode == 0o600


def test_load_master_key_no_generate_raises(tmp_path):
    """Test load master key no generate raises."""
    with pytest.raises(MasterKeyError, match="no master key"):
        load_or_create_master_key(
            path=tmp_path / "nope.key", generate_if_missing=False,
        )


def test_load_master_key_generate_does_not_clobber(tmp_path):
    """Test load master key generate does not clobber."""
    path = tmp_path / "master.key"
    raw = secrets.token_bytes(MASTER_KEY_BYTES)
    path.write_bytes(raw)
    mk = load_or_create_master_key(path=path)
    assert mk.key == raw  # the existing file wins, never overwritten


def test_rotate_re_encrypts_all_secrets(tmp_path):
    """Test rotate re encrypts all secrets."""
    db = StateStore(tmp_path / "state.db")
    db.initialize()
    db.upsert_user(user_id="alice", email="a@example.invalid")
    db.upsert_user(user_id="bob", email="b@example.invalid")

    old = AesGcmCipher(_key())
    new = AesGcmCipher(_key())

    a_token = b"alice-immich-token"
    b_token = b"bob-immich-token"
    a_apikey = b"alice-api-key"

    a = db.with_user("alice")
    b = db.with_user("bob")
    a.create_session(
        session_id="sa",
        encrypted_immich_token=old.encrypt(a_token),
        expires_at="2099-01-01T00:00:00Z",
    )
    b.create_session(
        session_id="sb",
        encrypted_immich_token=old.encrypt(b_token),
        expires_at="2099-01-01T00:00:00Z",
    )
    a.store_api_key(encrypted_key=old.encrypt(a_apikey), label="ak")

    counts = rotate_encrypted_columns(db._conn, old_cipher=old, new_cipher=new)
    assert counts == {"sessions": 2, "user_api_keys": 1}

    sessions = {row["session_id"]: row for row in a.list_sessions() + b.list_sessions()}
    assert new.decrypt(bytes(sessions["sa"]["encrypted_immich_token"])) == a_token
    assert new.decrypt(bytes(sessions["sb"]["encrypted_immich_token"])) == b_token
    keys = a.list_api_keys()
    assert new.decrypt(bytes(keys[0]["encrypted_key"])) == a_apikey

    # Old cipher can no longer read any of them.
    with pytest.raises(ValueError):
        old.decrypt(bytes(sessions["sa"]["encrypted_immich_token"]))

    db.close()


def test_rotate_aborts_atomically_on_decrypt_failure(tmp_path):
    """Test rotate aborts atomically on decrypt failure."""
    db = StateStore(tmp_path / "state.db")
    db.initialize()
    db.upsert_user(user_id="alice", email="a@example.invalid")

    old = AesGcmCipher(_key())
    bogus = AesGcmCipher(_key())  # not the cipher used to encrypt below
    new = AesGcmCipher(_key())

    a = db.with_user("alice")
    blob = old.encrypt(b"token")
    a.create_session(
        session_id="sa",
        encrypted_immich_token=blob,
        expires_at="2099-01-01T00:00:00Z",
    )

    with pytest.raises(ValueError):
        rotate_encrypted_columns(db._conn, old_cipher=bogus, new_cipher=new)

    # Row is unchanged; old still decrypts.
    sessions = a.list_sessions()
    assert bytes(sessions[0]["encrypted_immich_token"]) == blob
    assert old.decrypt(bytes(sessions[0]["encrypted_immich_token"])) == b"token"
    db.close()
