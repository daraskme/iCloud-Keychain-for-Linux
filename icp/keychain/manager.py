"""Exact-match iCloud Passwords edits with conditional saves and read-back checks."""

from __future__ import annotations

import plistlib

from . import pipeline, write
from .sidecar import edit_sidecar
from ..octagon.client import load_peer_keys
from ..transport import ckks


class PasswordEditError(ValueError):
    pass


def _snapshot(client):
    tlks, view_keys = client.fetch_recoverable_tlks()
    records = client.sync_keychain(zones=("Passwords", "Manatee"))
    records.setdefault("synckey", []).extend(view_keys)
    oct_state = client.record["octagon"]
    own_key = load_peer_keys(oct_state).encryption.private_key
    plain_tlks = pipeline.unwrap_tlkshares(
        records.get("tlkshare", []), oct_state["peer_id"], own_key)
    class_keys = pipeline.unwrap_class_keys(
        records.get("synckey", []), {**plain_tlks, **tlks})
    return records, class_keys


def _target(records, class_keys, site: str, username: str):
    matches = []
    for record in records.get("item", []):
        plain = pipeline.decrypt_items([record], class_keys)
        if not plain:
            continue
        item = plain[0]
        if item.get("srvr") == site and item.get("acct") == username:
            matches.append((record, item))
    logins = [(r, i) for r, i in matches if i.get("agrp") == "com.apple.cfnetwork"]
    sidecars = [(r, i) for r, i in matches
                if i.get("agrp") == "com.apple.password-manager"]
    if len(logins) != 1 or len(sidecars) > 1:
        raise PasswordEditError("exact site and username must identify one web login")
    return logins[0], sidecars[0] if sidecars else None


def edit_password(client, site: str, username: str, password: str) -> None:
    """Change an existing web login password, preserving all other item keys."""
    if not password:
        raise PasswordEditError("password cannot be empty")
    records, class_keys = _snapshot(client)
    (record, item), _ = _target(records, class_keys, site, username)
    parent = record.get_str("parentkeyref")
    class_key = class_keys.get(parent)
    if class_key is None:
        raise PasswordEditError("cannot decrypt the login's class key")
    changed = dict(item)
    changed["v_Data"] = password.encode("utf-8")
    candidate = write.encrypt_updated_item(record, class_key, changed)
    client.transport.save_record(ckks.build_record_save_request(candidate))
    _verify(client, record.record_name, class_keys,
            lambda current: current.get("v_Data") == changed["v_Data"])


def edit_metadata(client, site: str, username: str, *, notes: str | None = None,
                  totp_uri: str | None = None) -> None:
    """Edit notes and/or TOTP on an existing Password Manager Metadata sidecar."""
    if notes is None and totp_uri is None:
        raise PasswordEditError("select notes or TOTP to edit")
    records, class_keys = _snapshot(client)
    _, sidecar = _target(records, class_keys, site, username)
    if sidecar is None:
        raise PasswordEditError("this login has no Password Manager Metadata record yet")
    record, item = sidecar
    parent = record.get_str("parentkeyref")
    class_key = class_keys.get(parent)
    if class_key is None:
        raise PasswordEditError("cannot decrypt the metadata class key")
    original = item.get("v_Data")
    if not isinstance(original, bytes):
        raise PasswordEditError("metadata payload is not binary")
    changed = dict(item)
    changed["v_Data"] = edit_sidecar(original, notes=notes, totp_uri=totp_uri)
    expected = plistlib.loads(changed["v_Data"])
    candidate = write.encrypt_updated_item(record, class_key, changed)
    client.transport.save_record(ckks.build_record_save_request(candidate))
    _verify(client, record.record_name, class_keys,
            lambda current: plistlib.loads(current["v_Data"]) == expected)


def _verify(client, record_name: str, class_keys: dict, predicate) -> None:
    records = client.sync_keychain(zones=("Passwords", "Manatee"))
    candidates = [r for r in records.get("item", []) if r.record_name == record_name]
    if len(candidates) != 1:
        raise PasswordEditError("save returned, but the record was not found on read-back")
    current = pipeline.decrypt_items(candidates, class_keys)
    if len(current) != 1 or not predicate(current[0]):
        raise PasswordEditError("save returned, but iCloud read-back did not match")
