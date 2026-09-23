"""Exercise encrypted rename writes against a synthetic, etag-checking server."""
import os
import plistlib
import unittest
from unittest.mock import Mock, patch

from icp.keychain import manager, pipeline, write
from icp.proto.codec import Writer, decode_fields, first
from icp.transport import ckks


class RenameTests(unittest.TestCase):
    def setUp(self):
        self.key = os.urandom(64)
        self.keys = {"class-key": self.key}
        self.items = {}
        self.calls = 0
        self.fail_at = None
        self.commit_then_fail = False
        self.concurrent = False
        self.client = Mock()
        self.client.transport.save_record.side_effect = self.save
        self.client.transport.delete_record.side_effect = self.delete
        self.client.sync_keychain.side_effect = lambda **kw: {"item": list(self.items.values())}
        self.login = {"srvr": "old.example", "acct": "alice", "agrp": "com.apple.cfnetwork",
                      "labl": "old.example", "v_Data": b"secret", "custom": "keep"}
        self.meta = {"srvr": "old.example", "acct": "alice", "agrp": "com.apple.password-manager",
                     "v_Data": plistlib.dumps({"notes": b"memo", "custom": "keep"}, fmt=plistlib.FMT_BINARY)}
        self.add("login", self.login)
        self.add("metadata", self.meta)
        self.snapshot = patch.object(manager, "_snapshot", side_effect=lambda _: ({"item": list(self.items.values())}, self.keys))
        self.snapshot.start()
        self.addCleanup(self.snapshot.stop)

    def add(self, name, item):
        rid = Writer().message(1, Writer().string(1, name).uint64(2, 1)).message(
            2, ckks.record_zone_identifier("Passwords", "test-user"))
        raw = Writer().string(1, "etag-0").message(2, rid).message(3, Writer().string(1, "item"))
        fields = [("data", ckks.bytes_value(b"old")), ("wrappedkey", ckks.string_value("old")),
                  ("parentkeyref", Writer().uint64(1, 5).message(9, Writer().message(
                      2, Writer().message(1, Writer().string(1, "class-key"))))),
                  ("encver", Writer().uint64(1, 7).uint64(4, 2)),
                  ("gen", Writer().uint64(1, 7).uint64(4, 0))]
        for key, val in fields:
            raw.message(7, Writer().message(1, Writer().string(1, key)).message(2, val))
        record = ckks.parse_record(raw.finish())
        self.items[name] = ckks.parse_record(write.encrypt_updated_item(record, self.key, item))

    def plain(self, name):
        return pipeline.decrypt_items([self.items[name]], self.keys)[0]

    def save(self, request):
        self.calls += 1
        data = decode_fields(request)
        raw = first(data, 1)
        record = ckks.parse_record(raw)
        old = self.items.get(record.record_name)
        if old:
            self.assertEqual(first(data, 4).decode(), old.etag)
        if self.calls == self.fail_at and not self.commit_then_fail:
            if self.concurrent:
                item = self.plain("login")
                item["v_Data"] = b"other-device"
                self.add("login", item)
            raise OSError("offline")
        # Return a new etag on each accepted write, including recovery writes.
        fields = decode_fields(raw)
        updated = Writer().string(1, f"etag-{self.calls}")
        for field, values in fields.items():
            if field != 1:
                for value in values:
                    updated.message(field, value)
        self.items[record.record_name] = ckks.parse_record(updated.finish())
        if self.calls == self.fail_at:
            raise OSError("response lost")

    def rename(self, **kwargs):
        defaults = dict(password="secret", notes="memo", totp_uri="", expected_values=("secret", "memo", ""))
        defaults.update(kwargs)
        manager.rename_password(self.client, "old.example", "alice", "new.example", "bob", **defaults)

    def delete(self, request):
        data = decode_fields(request)
        rid = decode_fields(first(data, 1))
        name = first(decode_fields(first(rid, 1)), 1).decode()
        self.assertEqual(first(data, 2).decode(), self.items[name].etag)
        del self.items[name]

    def test_new_metadata_is_created_when_login_did_not_have_it(self):
        self.add("template", {**self.meta, "srvr": "template.example"})
        del self.items["metadata"]
        self.rename(expected_values=("secret", "", ""))
        names = set(self.items) - {"login", "template"}
        self.assertEqual(len(names), 1)
        added = self.plain(names.pop())
        self.assertEqual((added["srvr"], added["acct"]), ("new.example", "bob"))
        self.assertEqual(plistlib.loads(added["v_Data"])["notes"], b"memo")

    def test_new_metadata_lost_response_is_deleted_on_rollback(self):
        self.add("template", {**self.meta, "srvr": "template.example"})
        del self.items["metadata"]
        self.fail_at = 2
        self.commit_then_fail = True
        with self.assertRaisesRegex(manager.PasswordEditError, "変更は元に戻しました"):
            self.rename(expected_values=("secret", "", ""))
        self.assertEqual(set(self.items), {"login", "template"})
        self.assertEqual(self.plain("login"), self.login)

    def test_missing_metadata_template_prevents_login_write(self):
        del self.items["metadata"]
        with self.assertRaisesRegex(manager.PasswordEditError, "no usable"):
            self.rename(expected_values=("secret", "", ""))
        self.assertEqual(self.calls, 0)

    def test_rename_preserves_record_ids_secret_and_unknown_metadata(self):
        self.rename()
        self.assertEqual(set(self.items), {"login", "metadata"})
        for name in self.items:
            self.assertEqual((self.plain(name)["srvr"], self.plain(name)["acct"]), ("new.example", "bob"))
        self.assertEqual(self.plain("login")["v_Data"], b"secret")
        self.assertEqual(self.plain("login")["custom"], "keep")
        self.assertEqual(plistlib.loads(self.plain("metadata")["v_Data"]), plistlib.loads(self.meta["v_Data"]))

    def test_password_notes_and_totp_can_change_together(self):
        uri = "otpauth://totp/new.example:bob?secret=JBSWY3DPEHPK3PXP&issuer=new.example"
        self.rename(password="updated", notes="new memo", totp_uri=uri)
        self.assertEqual(self.plain("login")["v_Data"], b"updated")
        notes, totp = manager._metadata_values((self.items["metadata"], self.plain("metadata")))
        self.assertEqual(notes, "new memo")
        self.assertIn("JBSWY3DPEHPK3PXP", totp)

    def test_conflict_prevents_all_writes(self):
        self.add("duplicate", {**self.login, "srvr": "new.example", "acct": "bob"})
        with self.assertRaisesRegex(manager.PasswordEditError, "既に登録"):
            self.rename()
        self.assertEqual(self.calls, 0)

    def test_stale_original_prevents_all_writes(self):
        with self.assertRaisesRegex(manager.PasswordEditError, "別の端末"):
            self.rename(expected_values=("stale", "memo", ""))
        self.assertEqual(self.calls, 0)

    def test_failed_second_write_rolls_back_login(self):
        self.fail_at = 2
        with self.assertRaisesRegex(manager.PasswordEditError, "変更は元に戻しました"):
            self.rename()
        self.assertEqual(self.plain("login"), self.login)
        self.assertEqual(self.plain("metadata"), self.meta)

    def test_lost_response_restores_both_committed_records(self):
        self.fail_at = 2
        self.commit_then_fail = True
        with self.assertRaisesRegex(manager.PasswordEditError, "変更は元に戻しました"):
            self.rename()
        self.assertEqual(self.plain("login"), self.login)
        self.assertEqual(self.plain("metadata"), self.meta)

    def test_rollback_does_not_overwrite_other_device(self):
        self.fail_at = 2
        self.concurrent = True
        with self.assertRaisesRegex(manager.PasswordEditError, "一部の変更を元に戻せません"):
            self.rename()
        self.assertEqual(self.plain("login")["v_Data"], b"other-device")

    def test_rename_without_metadata_needs_no_template(self):
        del self.items["metadata"]
        self.rename(notes="", expected_values=("secret", "", ""))
        self.assertEqual(set(self.items), {"login"})
        self.assertEqual(self.plain("login")["srvr"], "new.example")

    def test_invalid_totp_prevents_all_writes(self):
        with self.assertRaises(ValueError):
            self.rename(totp_uri="invalid")
        self.assertEqual(self.calls, 0)
