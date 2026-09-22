"""Generate site passwords from the operating system's cryptographic random source."""

import secrets
import string


_GROUPS = (string.ascii_lowercase, string.ascii_uppercase, string.digits,
           "!@#$%^&*-_=+?")
_ALPHABET = "".join(_GROUPS)


def generate(length: int = 24) -> str:
    """Return a random password containing every character class."""
    if not 12 <= length <= 128:
        raise ValueError("password length must be between 12 and 128")
    while True:
        value = "".join(secrets.choice(_ALPHABET) for _ in range(length))
        if all(any(c in group for c in value) for group in _GROUPS):
            return value
