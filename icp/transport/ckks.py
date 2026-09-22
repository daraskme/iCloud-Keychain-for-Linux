"""CKKS record fetch: pull the iCloud Keychain zone records (tlkshare / synckey / item /
currentitem) via a CloudKit RecordRetrieveChanges op per zone. See RESEARCH.md "transport/ckks.py"."""

from __future__ import annotations

import dataclasses
import struct

from ..proto.codec import Writer, decode_fields, encode_varint, first, first_str


@dataclasses.dataclass
class CKDate:
    """A CloudKit Date value - kept distinct from a plain double so the item AAD encodes it
    as RFC3339 rather than as a number."""
    time: float   # seconds (UNIX)


# Keychain CKKS zones (private DB). "Passwords" + "Manatee" hold web credentials.
KEYCHAIN_ZONES = (
    "Passwords", "Manatee", "Engram", "SecureObjectSync", "ProtectedCloudStorage",
    "CreditCards", "ApplePay", "WiFi", "Home", "Groups", "Contacts", "Mail",
    "LimitedPeersAllowed", "SE-PTC", "Photos",
)

ID_TYPE_RECORD_ZONE = 6
ID_TYPE_USER = 7

OP_TYPE_RECORD_RETRIEVE_CHANGES = 213
FIELD_RETRIEVE_CHANGES = 213
OP_TYPE_RECORD_SAVE = 210
FIELD_RECORD_SAVE = 210
OP_TYPE_RECORD_DELETE = 214
FIELD_RECORD_DELETE = 214


def _identifier(name: str, type_: int) -> bytes:
    return Writer().string(1, name).uint64(2, type_).finish()


def record_zone_identifier(zone: str, user_id: str) -> bytes:
    return (Writer()
            .message(1, _identifier(zone, ID_TYPE_RECORD_ZONE))
            .message(2, _identifier(user_id, ID_TYPE_USER))
            .finish())


def build_retrieve_changes_request(zone_identifier: bytes,
                                   continuation_token: bytes | None = None,
                                   max_changes: int = 500) -> bytes:
    return (Writer()
            .bytes(1, continuation_token)
            .message(2, zone_identifier)
            .uint64(4, max_changes)
            .finish())


@dataclasses.dataclass
class CloudKitRecord:
    record_name: str        # identifier value name (usually a UUID)
    type: str               # "item" / "synckey" / "tlkshare" / "currentitem" / ...
    fields: dict            # {field_name: python value}
    etag: str = ""           # CloudKit change tag for conditional saves
    raw: bytes = b""        # original record, including unknown fields and zone identifier

    def get_bytes(self, name: str) -> bytes | None:
        v = self.fields.get(name)
        return v if isinstance(v, (bytes, bytearray)) else None

    def get_str(self, name: str) -> str | None:
        v = self.fields.get(name)
        return v if isinstance(v, str) else None


def _parse_value(raw: bytes):
    """Record.Field.Value -> python value. Types are preserved so the item AAD encodes each
    correctly: str, bytes, int, float, CKDate."""
    v = decode_fields(raw)
    if 2 in v:
        return first(v, 2)                     # bytesValue
    if 7 in v:
        return first_str(v, 7)                 # stringValue
    if 4 in v:
        return first(v, 4)                     # signedValue (int64)
    if 5 in v:                                 # doubleValue (fixed64, IEEE-754)
        return struct.unpack("<d", first(v, 5).to_bytes(8, "little"))[0]
    if 6 in v:                                 # dateValue: Date{ time(1): double }
        d = decode_fields(first(v, 6))
        if 1 in d:
            return CKDate(struct.unpack("<d", first(d, 1).to_bytes(8, "little"))[0])
    if 9 in v:                                 # referenceValue -> the target record's name,
        ref = decode_fields(first(v, 9))       # e.g. parentkeyref linking synckey->TLK / item->classkey
        rid = first(ref, 2)
        if rid is not None:
            idval = first(decode_fields(rid), 1)
            if idval is not None:
                return first_str(decode_fields(idval), 1)
    return None


def parse_record(raw: bytes) -> CloudKitRecord:
    f = decode_fields(raw)
    rec_id = first(f, 2)
    name = ""
    if rec_id is not None:
        idval = first(decode_fields(rec_id), 1)
        if idval is not None:
            name = first_str(decode_fields(idval), 1) or ""
    type_blob = first(f, 3)
    rtype = first_str(decode_fields(type_blob), 1) if type_blob is not None else ""
    fields = {}
    for fld in f.get(7, []):
        ff = decode_fields(fld)
        ident = first(ff, 1)
        fname = first_str(decode_fields(ident), 1) if ident is not None else None
        val = first(ff, 2)
        if fname is not None and val is not None:
            fields[fname] = _parse_value(val)
    return CloudKitRecord(record_name=name, type=rtype or "", fields=fields,
                          etag=first_str(f, 1) or "", raw=raw)


def parse_retrieve_changes_response(raw: bytes) -> dict:
    """Returns {records, continuation_token, status}. status 1 means another page is available;
    status 3 is the final state token for the zone."""
    f = decode_fields(raw)
    records = []
    for change in f.get(1, []):
        cf = decode_fields(change)
        rec = first(cf, 5)
        if rec is not None:
            records.append(parse_record(rec))
    return {"records": records, "continuation_token": first(f, 2), "status": first(f, 4)}


def _wire_segments(raw: bytes):
    """Yield (field number, complete wire bytes, value) without discarding unknown fields."""
    pos = 0
    while pos < len(raw):
        start = pos
        tag, pos = _read_varint(raw, pos)
        field, wire = tag >> 3, tag & 7
        if wire == 2:
            size, pos = _read_varint(raw, pos)
            end = pos + size
            if end > len(raw):
                raise ValueError("truncated protobuf field")
            value = raw[pos:end]
            pos = end
        elif wire == 0:
            value, pos = _read_varint(raw, pos)
        elif wire in (1, 5):
            size = 8 if wire == 1 else 4
            end = pos + size
            if end > len(raw):
                raise ValueError("truncated protobuf field")
            value = raw[pos:end]
            pos = end
        else:
            raise ValueError(f"unsupported protobuf wire type {wire}")
        yield field, raw[start:pos], value


def _read_varint(raw: bytes, pos: int) -> tuple[int, int]:
    value = shift = 0
    while pos < len(raw) and shift < 70:
        byte = raw[pos]
        pos += 1
        value |= (byte & 0x7f) << shift
        if not byte & 0x80:
            return value, pos
        shift += 7
    raise ValueError("truncated or oversized protobuf varint")


def replace_record_fields(record: CloudKitRecord, replacements: dict[str, bytes]) -> bytes:
    """Replace complete encoded Record.Field messages, preserving every other wire field.

    ``replacements`` contains complete Field.Value messages. Only existing fields may be
    changed, and a stale or absent etag is rejected before a save request is made.
    """
    if not record.raw or not record.etag or record.type != "item":
        raise ValueError("an item record with raw bytes and etag is required")
    remaining = set(replacements)
    result = bytearray()
    for number, segment, value in _wire_segments(record.raw):
        if number == 7:
            field = decode_fields(value)
            identifier = first(field, 1)
            name = first_str(decode_fields(identifier), 1) if identifier else None
            if name in remaining:
                replacement = (Writer().message(1, identifier)
                               .message(2, replacements[name]).finish())
                result += encode_varint((7 << 3) | 2)
                result += encode_varint(len(replacement))
                result += replacement
                remaining.remove(name)
                continue
        result += segment
    if remaining:
        raise ValueError(f"record fields absent: {', '.join(sorted(remaining))}")
    return bytes(result)


def bytes_value(data: bytes) -> bytes:
    return Writer().uint64(1, 1).bytes(2, data).finish()


def string_value(data: str) -> bytes:
    return Writer().uint64(1, 3).string(7, data).finish()


def build_record_save_request(record_raw: bytes) -> bytes:
    """Save a complete record with its original etag (CloudKit conflict detection)."""
    parsed = parse_record(record_raw)
    if not parsed.etag or not parsed.record_name:
        raise ValueError("record save requires an etag and record identifier")
    # The CAS etag belongs to RecordSaveRequest field 4 as well as Record field 1.
    # saveSemantics=1 means failIfOutdated; omitting field 4 accepted a stale write.
    return (Writer().message(1, record_raw).bool(2, True)
            .string(4, parsed.etag).uint64(6, 1).finish())


def build_record_create_request(record_raw: bytes) -> bytes:
    """Create a new record, failing if its identifier already exists."""
    parsed = parse_record(record_raw)
    if parsed.etag or not parsed.record_name or parsed.type != "item":
        raise ValueError("new item record must have an ID and no etag")
    return Writer().message(1, record_raw).bool(2, True).uint64(6, 2).finish()


def build_new_item_record(template: CloudKitRecord, name: str,
                          data: bytes, wrappedkey: str) -> bytes:
    """Construct a new `item` in the template's zone with its parent class key.

    Server-managed fields and the template's etag are omitted. The only copied
    item fields are the class-key reference and CKKS format counters.
    """
    if template.type != "item" or not template.raw or not name:
        raise ValueError("item template and new identifier are required")
    source = decode_fields(template.raw)
    source_id = first(source, 2)
    zone = first(decode_fields(source_id), 2) if source_id else None
    if zone is None:
        raise ValueError("template record has no zone")
    rid = (Writer().message(1, _identifier(name, 1))
           .message(2, zone).finish())
    record = Writer().message(2, rid).message(3, Writer().string(1, "item"))
    copied = set()
    for field_raw in source.get(7, []):
        field = decode_fields(field_raw)
        identifier = first(field, 1)
        fname = first_str(decode_fields(identifier), 1) if identifier else None
        if fname == "data":
            record.message(7, Writer().message(1, identifier)
                           .message(2, bytes_value(data)))
        elif fname == "wrappedkey":
            record.message(7, Writer().message(1, identifier)
                           .message(2, string_value(wrappedkey)))
        elif fname in {"parentkeyref", "encver", "gen", "uploadver"}:
            record.message(7, field_raw)
        else:
            continue
        copied.add(fname)
    if not {"data", "wrappedkey", "parentkeyref", "encver", "gen"} <= copied:
        raise ValueError("template is missing required CKKS item fields")
    return record.finish()


def build_record_delete_request(record: CloudKitRecord) -> bytes:
    """Delete one exact record using its current etag as a compare-and-swap guard."""
    if not record.raw or not record.etag or not record.record_name:
        raise ValueError("record delete requires raw bytes, identifier, and etag")
    identifier = first(decode_fields(record.raw), 2)
    if identifier is None:
        raise ValueError("record identifier is missing")
    return Writer().message(1, identifier).string(2, record.etag).finish()
