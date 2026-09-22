"""Lossless edits to a Password Manager Metadata sidecar's binary plist payload.

This module only prepares bytes. Uploading them requires a conditional CKKS write.
"""

from __future__ import annotations

import base64
import binascii
import plistlib
import urllib.parse


_ALGORITHMS = {"SHA1": 0, "SHA256": 1, "SHA512": 2}


def totp_from_uri(uri: str) -> dict:
    """Convert an otpauth URI to Apple's structured TOTP sidecar value."""
    parsed = urllib.parse.urlsplit(uri)
    if parsed.scheme != "otpauth" or parsed.netloc != "totp":
        raise ValueError("expected an otpauth://totp URI")
    values = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)

    def one(name: str, default: str = "") -> str:
        entries = values.get(name, [default])
        if len(entries) != 1:
            raise ValueError(f"duplicate {name} parameter")
        return entries[0]

    encoded = one("secret").replace(" ", "").upper()
    try:
        secret = base64.b32decode(encoded + "=" * (-len(encoded) % 8), casefold=True)
    except binascii.Error as exc:
        raise ValueError("invalid TOTP secret") from exc
    if not secret:
        raise ValueError("TOTP secret is empty")
    algorithm = one("algorithm", "SHA1").upper()
    if algorithm not in _ALGORITHMS:
        raise ValueError("unsupported TOTP algorithm")
    try:
        digits = int(one("digits", "6"))
        period = int(one("period", "30"))
    except ValueError as exc:
        raise ValueError("invalid TOTP digits or period") from exc
    if digits not in (6, 7, 8) or not 1 <= period <= 3600:
        raise ValueError("TOTP digits or period out of range")
    label = urllib.parse.unquote(parsed.path.lstrip("/"))
    label_issuer, sep, account = label.partition(":")
    issuer = one("issuer") or (label_issuer if sep else "")
    account = account if sep else label
    return {"secret": secret, "algorithm": _ALGORITHMS[algorithm],
            "digits": digits, "period": period, "issuer": issuer,
            "accountName": account, "originalURL": uri}


def edit_sidecar(payload: bytes, *, notes: str | None = None,
                 totp_uri: str | None = None) -> bytes:
    """Edit notes/TOTP while preserving unrelated sidecar fields and their types.

    ``None`` leaves a field untouched; an empty string clears it. The original payload
    must be a binary plist dictionary, so unexpected records fail closed.
    """
    if not payload.startswith(b"bplist00"):
        raise ValueError("sidecar payload is not a binary plist")
    data = plistlib.loads(payload)
    if not isinstance(data, dict):
        raise ValueError("sidecar payload is not a dictionary")
    if notes is not None:
        if not isinstance(notes, str):
            raise TypeError("notes must be text")
        if notes:
            data["notes"] = notes.encode("utf-8")
        else:
            data.pop("notes", None)
    if totp_uri is not None:
        if totp_uri:
            replacement = totp_from_uri(totp_uri)
            previous = data.get("totp")
            if isinstance(previous, dict):
                data["totp"] = {**previous, **replacement}
            else:
                data["totp"] = replacement
        else:
            data.pop("totp", None)
    return plistlib.dumps(data, fmt=plistlib.FMT_BINARY)
