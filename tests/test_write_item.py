import base64
import os
import plistlib
import unittest

from icp.keychain import crypto, pipeline, write
from icp.proto.codec import Writer, decode_fields, first
from icp.transport import ckks


class WriteItemTests(unittest.TestCase):
    def test_encrypt_updated_item_preserves_metadata_and_round_trips(self):
        key = os.urandom(64)
        zone = ckks.record_zone_identifier("Passwords", "user-1")
        record_id = (Writer().message(1, Writer().string(1, "item-1").uint64(2, 1))
                     .message(2, zone).finish())
        fields = [
            ("data", ckks.bytes_value(b"old")),
            ("wrappedkey", ckks.string_value("old-key")),
            ("parentkeyref", Writer().uint64(1, 5).message(
                9, Writer().message(2, Writer().message(
                    1, Writer().string(1, "class-key")))).finish()),
            ("encver", Writer().uint64(1, 7).uint64(4, 2).finish()),
            ("gen", Writer().uint64(1, 7).uint64(4, 0).finish()),
        ]
        raw = (Writer().string(1, "etag-1").message(2, record_id)
               .message(3, Writer().string(1, "item")))
        for name, value in fields:
            raw.message(7, Writer().message(1, Writer().string(1, name)).message(2, value))
        record = ckks.parse_record(raw.finish())
        item = {"agrp": "com.apple.password-manager", "v_Data": b"bplist00"}
        updated = ckks.parse_record(write.encrypt_updated_item(record, key, item))
        self.assertEqual(updated.etag, "etag-1")
        self.assertEqual(updated.get_str("parentkeyref"), "class-key")
        self.assertEqual(updated.fields["encver"], 2)
        wrapped = base64.b64decode(updated.get_str("wrappedkey"))
        item_key = crypto.siv_unwrap(key, wrapped)
        aad = pipeline.authenticated_data_v2(
            updated.record_name, updated.fields, encver=2, gen=0,
            parent_key_id="class-key")
        self.assertEqual(plistlib.loads(crypto.decrypt_item(
            item_key, updated.get_bytes("data"), aad)), item)

        new_raw = write.encrypt_new_item(record, key, "new-item", item)
        created = ckks.parse_record(new_raw)
        self.assertEqual(created.record_name, "new-item")
        self.assertEqual(created.etag, "")
        self.assertEqual(pipeline.decrypt_items([created], {"class-key": key}), [item])
        request = ckks.build_record_create_request(new_raw)
        self.assertEqual(first(decode_fields(request), 6), 2)
