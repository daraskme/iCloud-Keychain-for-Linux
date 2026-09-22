import plistlib
import unittest

from icp.keychain.sidecar import edit_sidecar, totp_from_uri


class SidecarEditTests(unittest.TestCase):
    def test_edits_notes_and_totp_without_losing_other_data(self):
        original = {"notes": b"old", "ctxt": {"": {"lUsed": 123.0}},
                    "s_hi": [{"p": "previous password"}],
                    "totp": {"_initialDate": "keep"}}
        payload = plistlib.dumps(original, fmt=plistlib.FMT_BINARY)
        uri = "otpauth://totp/Example%3Aalice?secret=GEZDGNBVGY3TQOJQ&issuer=Example"
        edited = plistlib.loads(edit_sidecar(payload, notes="新しいメモ", totp_uri=uri))
        self.assertEqual(edited["notes"], "新しいメモ".encode())
        self.assertEqual(edited["ctxt"], original["ctxt"])
        self.assertEqual(edited["s_hi"], original["s_hi"])
        self.assertEqual(edited["totp"]["_initialDate"], "keep")
        self.assertEqual(edited["totp"]["secret"], b"1234567890")
        self.assertEqual(edited["totp"]["accountName"], "alice")

    def test_clear_only_requested_fields(self):
        payload = plistlib.dumps({"notes": b"old", "totp": {"secret": b"abc"},
                                  "s_as": [1]}, fmt=plistlib.FMT_BINARY)
        edited = plistlib.loads(edit_sidecar(payload, totp_uri=""))
        self.assertEqual(edited["notes"], b"old")
        self.assertNotIn("totp", edited)
        self.assertEqual(edited["s_as"], [1])

    def test_rejects_invalid_enrolment(self):
        for uri in ("https://example.com", "otpauth://totp/x?secret=bad!",
                    "otpauth://totp/x?secret=GEZDGNBVGY3TQOJQ&digits=0"):
            with self.subTest(uri=uri), self.assertRaises(ValueError):
                totp_from_uri(uri)

    def test_rejects_non_sidecar_payload(self):
        with self.assertRaises(ValueError):
            edit_sidecar(b"password", notes="note")
