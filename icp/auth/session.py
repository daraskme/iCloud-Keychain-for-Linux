"""Encrypted session store: persistent auth artifacts encrypted with a libsodium secret box,
the master key held in the GNOME login keyring (Secret Service), or a 0600 key file if absent."""

import json
import logging

import nacl.secret
import nacl.utils

from .. import paths

logger = logging.getLogger(__name__)

_ATTRS = {"application": "icp", "type": "master-key"}
_LABEL = "ApplePasswords-Linux master key"


def _key_from_secret_service() -> bytes | None:
    try:
        import secretstorage
    except Exception:
        return None
    try:
        conn = secretstorage.dbus_init()
        coll = secretstorage.get_default_collection(conn)
        if coll.is_locked():
            coll.unlock()
        for item in coll.search_items(_ATTRS):
            return item.get_secret()
        key = nacl.utils.random(nacl.secret.SecretBox.KEY_SIZE)
        coll.create_item(_LABEL, _ATTRS, key, replace=True)
        return key
    except Exception as e:  # dbus not running, no keyring, etc.
        logger.warning("Secret Service unavailable (%s); using key file fallback", e)
        return None


def _master_key() -> bytes:
    key = _key_from_secret_service()
    if key is not None:
        return key
    f = paths.fallback_key_file()
    if f.exists():
        return f.read_bytes()
    key = nacl.utils.random(nacl.secret.SecretBox.KEY_SIZE)
    f.write_bytes(key)
    f.chmod(0o600)
    logger.warning("Stored master key at %s (0600) - less safe than the keyring", f)
    return key


def save(data: dict) -> None:
    box = nacl.secret.SecretBox(_master_key())
    blob = box.encrypt(json.dumps(data).encode())
    f = paths.session_file()
    f.write_bytes(blob)
    f.chmod(0o600)


def load() -> dict | None:
    f = paths.session_file()
    if not f.exists():
        return None
    box = nacl.secret.SecretBox(_master_key())
    return json.loads(box.decrypt(f.read_bytes()).decode())


def clear() -> None:
    f = paths.session_file()
    if f.exists():
        f.unlink()
