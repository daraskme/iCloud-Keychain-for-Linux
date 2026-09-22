import unittest

from icp.keychain.manager import _new_item
from icp.octagon.client import OctagonClient
from icp.transport.cloudkit import CloudKitError


class NewItemTests(unittest.TestCase):
    def test_new_login_does_not_copy_old_secret_or_unknown_fields(self):
        template = {"agrp": "com.apple.cfnetwork", "class": "inet",
                    "desc": "Web form password", "musr": b"", "path": "",
                    "v_Data": b"old secret", "srvr": "old.example",
                    "acct": "old user", "custom": "old metadata"}
        item = _new_item(template, "new.example", "alice", b"new secret",
                         metadata=False)
        self.assertEqual(item["v_Data"], b"new secret")
        self.assertEqual(item["srvr"], "new.example")
        self.assertEqual(item["acct"], "alice")
        self.assertEqual(item["labl"], "new.example")
        self.assertNotIn("custom", item)
        self.assertEqual(len(item["sha1"]), 20)

    def test_metadata_label_matches_passwords_format(self):
        item = _new_item({"agrp": "com.apple.password-manager"},
                         "new.example", "alice", b"bplist00", metadata=True)
        self.assertEqual(item["labl"],
                         "Password Manager Metadata: new.example (alice)")

    def test_strict_record_fetch_does_not_hide_server_errors(self):
        class FailingTransport:
            def fetch_records(self, _request):
                raise CloudKitError("test failure")

        client = object.__new__(OctagonClient)
        client.user_id = "user-1"
        client.transport = FailingTransport()
        with self.assertRaises(CloudKitError):
            client.sync_keychain(zones=("Passwords",), strict=True)
