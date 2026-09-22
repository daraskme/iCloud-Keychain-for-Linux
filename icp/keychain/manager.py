"""Exact-match iCloud Passwords edits with conditional saves and read-back checks."""

from __future__ import annotations

import plistlib
import datetime
import os
import uuid

from . import pipeline, write
from .sidecar import edit_sidecar
from ..octagon.client import load_peer_keys
from ..transport import ckks


class PasswordEditError(ValueError):
    pass


def _snapshot(client):
    tlks, view_keys = client.fetch_recoverable_tlks()
    records = client.sync_keychain(zones=("Passwords", "Manatee"), strict=True)
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
    """Edit notes and/or TOTP, creating a metadata sidecar when needed."""
    if notes is None and totp_uri is None:
        raise PasswordEditError("select notes or TOTP to edit")
    records, class_keys = _snapshot(client)
    _, sidecar = _target(records, class_keys, site, username)
    if sidecar is None:
        template = _template(records, class_keys, "com.apple.password-manager")
        payload = edit_sidecar(plistlib.dumps({}, fmt=plistlib.FMT_BINARY),
                               notes=notes, totp_uri=totp_uri)
        item = _new_item(template[1], site, username, payload, metadata=True)
        name = str(uuid.uuid4()).upper()
        try:
            _create_one(client, template, class_keys, name, item)
        except Exception as exc:
            try:
                _rollback_created(client, (name,))
            except Exception as rollback_error:
                raise PasswordEditError(
                    f"metadata creation failed and rollback failed: {rollback_error}") from exc
            raise PasswordEditError(f"metadata creation failed: {exc}") from exc
        return
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
    records = client.sync_keychain(zones=("Passwords", "Manatee"), strict=True)
    candidates = [r for r in records.get("item", []) if r.record_name == record_name]
    if len(candidates) != 1:
        raise PasswordEditError("save returned, but the record was not found on read-back")
    current = pipeline.decrypt_items(candidates, class_keys)
    if len(current) != 1 or not predicate(current[0]):
        raise PasswordEditError("save returned, but iCloud read-back did not match")


def _template(records, class_keys, group: str):
    for record in records.get("item", []):
        if record.fields.get("encver") != 2:
            continue
        parent = record.get_str("parentkeyref")
        if parent not in class_keys:
            continue
        plain = pipeline.decrypt_items([record], class_keys)
        if (len(plain) == 1 and plain[0].get("agrp") == group and
                isinstance(plain[0].get("v_Data"), bytes) and
                plain[0].get("musr", b"") == b"" and
                plain[0].get("path", "") == ""):
            return record, plain[0]
    raise PasswordEditError(f"no usable {group} item template in this keychain")


_INVARIANT_KEYS = ("agrp", "atyp", "class", "desc", "musr", "path",
                   "pdmn", "port", "ptcl", "sdmn", "tomb", "type")


def _new_item(template_item: dict, site: str, username: str,
              payload: bytes, *, metadata: bool) -> dict:
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    item = {key: template_item[key] for key in _INVARIANT_KEYS if key in template_item}
    item.update({"srvr": site, "acct": username,
                 "labl": (f"Password Manager Metadata: {site} ({username})"
                          if metadata else site),
                 "v_Data": payload, "sha1": os.urandom(20),
                 "cdat": now, "mdat": now})
    return item


def _create_one(client, template, class_keys, name: str, item: dict):
    record, _ = template
    parent = record.get_str("parentkeyref")
    raw = write.encrypt_new_item(record, class_keys[parent], name, item)
    client.transport.save_record(ckks.build_record_create_request(raw))
    _verify(client, name, class_keys, lambda current: current == item)


def _rollback_created(client, names: tuple[str, ...]) -> None:
    """Remove any newly created records found by their fresh UUIDs."""
    for name in reversed(names):
        fresh = client.sync_keychain(zones=("Passwords", "Manatee"), strict=True)
        matches = [r for r in fresh.get("item", []) if r.record_name == name]
        if len(matches) > 1:
            raise PasswordEditError("rollback found duplicate record identifiers")
        if matches:
            client.transport.delete_record(
                ckks.build_record_delete_request(matches[0]))
            _verify_absent(client, name)


def create_password(client, site: str, username: str, password: str,
                    *, notes: str = "", totp_uri: str = "") -> None:
    """Create a web login and its metadata sidecar, verifying each server write."""
    if not site or "." not in site or not username or not password:
        raise PasswordEditError("site, username, and password are required")
    records, class_keys = _snapshot(client)
    for record in records.get("item", []):
        plain = pipeline.decrypt_items([record], class_keys)
        if plain and plain[0].get("srvr") == site and plain[0].get("acct") == username:
            raise PasswordEditError("a login or metadata record already uses this site and username")
    login_template = _template(records, class_keys, "com.apple.cfnetwork")
    metadata_template = _template(records, class_keys, "com.apple.password-manager")
    login = _new_item(login_template[1], site, username,
                      password.encode("utf-8"), metadata=False)
    metadata_payload = edit_sidecar(
        plistlib.dumps({}, fmt=plistlib.FMT_BINARY),
        notes=notes if notes else None, totp_uri=totp_uri if totp_uri else None)
    metadata = _new_item(metadata_template[1], site, username,
                         metadata_payload, metadata=True)
    login_name = str(uuid.uuid4()).upper()
    metadata_name = str(uuid.uuid4()).upper()
    try:
        _create_one(client, login_template, class_keys, login_name, login)
        _create_one(client, metadata_template, class_keys, metadata_name, metadata)
    except Exception as exc:
        try:
            _rollback_created(client, (login_name, metadata_name))
        except Exception as rollback_error:
            raise PasswordEditError(
                f"creation failed and rollback also failed: {rollback_error}") from exc
        raise PasswordEditError(f"creation failed; new records rolled back: {exc}") from exc


def _verify_absent(client, record_name: str) -> None:
    records = client.sync_keychain(zones=("Passwords", "Manatee"), strict=True)
    if any(r.record_name == record_name for r in records.get("item", [])):
        raise PasswordEditError("delete returned, but record remains on iCloud")


def delete_password(client, site: str, username: str) -> None:
    """Delete a web login and its sidecar with etag-guarded record deletes."""
    records, class_keys = _snapshot(client)
    login, sidecar = _target(records, class_keys, site, username)
    if sidecar is not None:
        client.transport.delete_record(ckks.build_record_delete_request(sidecar[0]))
        _verify_absent(client, sidecar[0].record_name)
    try:
        client.transport.delete_record(ckks.build_record_delete_request(login[0]))
        _verify_absent(client, login[0].record_name)
    except Exception as exc:
        if sidecar is not None:
            raise PasswordEditError(
                f"metadata was deleted, but login deletion failed: {exc}") from exc
        raise
