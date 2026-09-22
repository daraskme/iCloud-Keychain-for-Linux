"""Prepare conditional CKKS item updates without sending them to CloudKit."""

from __future__ import annotations

import base64
import os
import plistlib

from . import crypto, pipeline
from ..transport import ckks


def encrypt_updated_item(record: ckks.CloudKitRecord, class_key: bytes,
                         item: dict) -> bytes:
    """Return an etagged Record with a newly encrypted item plist.

    The caller must have decrypted and checked the exact target record first. This preserves
    every existing Record field and replaces only ``data`` and ``wrappedkey``.
    """
    if record.type != "item" or record.fields.get("encver") != 2:
        raise ValueError("only CKKS encryption version 2 item records are supported")
    if not record.etag or not record.raw or len(class_key) != 64:
        raise ValueError("missing etag, raw record, or class key")
    parent = record.get_str("parentkeyref")
    if not parent or not isinstance(item.get("v_Data"), bytes):
        raise ValueError("missing parent key or item payload")
    new_item_key = os.urandom(64)
    wrapped = base64.b64encode(crypto.siv_wrap(class_key, new_item_key)).decode("ascii")
    aad = pipeline.authenticated_data_v2(
        record.record_name, record.fields,
        encver=record.fields["encver"], gen=record.fields.get("gen", 0),
        parent_key_id=parent)
    plaintext = plistlib.dumps(item, fmt=plistlib.FMT_BINARY)
    encrypted = crypto.encrypt_item(
        new_item_key, plaintext, aad, short_password=len(item["v_Data"]) < 20)
    if plistlib.loads(crypto.decrypt_item(new_item_key, encrypted, aad)) != item:
        raise ValueError("encrypted item failed local verification")
    return ckks.replace_record_fields(record, {
        "data": ckks.bytes_value(encrypted),
        "wrappedkey": ckks.string_value(wrapped),
    })


def encrypt_new_item(template: ckks.CloudKitRecord, class_key: bytes,
                     name: str, item: dict) -> bytes:
    """Prepare a new CKKS item using the template's zone and class-key reference."""
    if template.type != "item" or template.fields.get("encver") != 2:
        raise ValueError("only CKKS v2 item templates are supported")
    parent = template.get_str("parentkeyref")
    if not parent or len(class_key) != 64 or not isinstance(item.get("v_Data"), bytes):
        raise ValueError("missing parent, class key, or item payload")
    item_key = os.urandom(64)
    wrapped = base64.b64encode(crypto.siv_wrap(class_key, item_key)).decode("ascii")
    new_fields = {name: value for name, value in template.fields.items()
                  if name in {"parentkeyref", "encver", "gen", "uploadver"}}
    aad = pipeline.authenticated_data_v2(
        name, new_fields, encver=2, gen=new_fields.get("gen", 0),
        parent_key_id=parent)
    plaintext = plistlib.dumps(item, fmt=plistlib.FMT_BINARY)
    encrypted = crypto.encrypt_item(
        item_key, plaintext, aad, short_password=len(item["v_Data"]) < 20)
    raw = ckks.build_new_item_record(template, name, encrypted, wrapped)
    parsed = ckks.parse_record(raw)
    if pipeline.decrypt_items([parsed], {parent: class_key}) != [item]:
        raise ValueError("new encrypted item failed local verification")
    return raw
