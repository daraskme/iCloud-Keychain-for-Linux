"""Password generation requirements."""

import string
import unittest

from icp.passwords import generate


class PasswordGenerationTests(unittest.TestCase):
    def test_length_and_character_classes(self):
        for length in (12, 24, 128):
            password = generate(length)
            self.assertEqual(len(password), length)
            self.assertTrue(any(c in string.ascii_lowercase for c in password))
            self.assertTrue(any(c in string.ascii_uppercase for c in password))
            self.assertTrue(any(c in string.digits for c in password))
            self.assertTrue(any(c in "!@#$%^&*-_=+?" for c in password))

    def test_rejects_unsupported_lengths(self):
        for length in (0, 11, 129):
            with self.assertRaises(ValueError):
                generate(length)


if __name__ == "__main__":
    unittest.main()
