import os
import unittest

from icp.keychain.crypto import decrypt_item, encrypt_item, siv_unwrap, siv_wrap


class ItemEncryptionTests(unittest.TestCase):
    def test_siv_key_wrap_round_trip(self):
        parent = os.urandom(64)
        child = os.urandom(64)
        self.assertEqual(siv_unwrap(parent, siv_wrap(parent, child)), child)

    def test_item_round_trip_and_padding(self):
        key = os.urandom(64)
        aad = [b"UUID", b"metadata"]
        plain = b"bplist00synthetic"
        for short in (False, True):
            with self.subTest(short=short):
                encrypted = encrypt_item(key, plain, aad, short_password=short)
                self.assertEqual(decrypt_item(key, encrypted, aad), plain)
                self.assertEqual((len(encrypted) - 32) % 20, 0)

    def test_aad_tamper_fails(self):
        key = os.urandom(64)
        encrypted = encrypt_item(key, b"synthetic", [b"original"])
        with self.assertRaises(Exception):
            decrypt_item(key, encrypted, [b"changed"])
