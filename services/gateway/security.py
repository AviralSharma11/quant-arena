"""Password hashing.

Argon2id via argon2-cffi, with the library's own defaults, which follow current OWASP guidance
(Open Issue 015 sub-decision 15b). Nothing here is hand-rolled: choosing parameters by hand is
how a password hash quietly becomes a fast one.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import (
    InvalidHashError,
    VerificationError,
    VerifyMismatchError,
)

_hasher = PasswordHasher()

#: Every hash argon2-cffi produces starts with this. Asserted in the tests, because Success
#: Criterion 4 is "no plaintext or general-purpose hash appears anywhere" and the cheapest way
#: to be sure is to look at what actually landed in the column.
ARGON2ID_PREFIX = "$argon2id$"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """False for a wrong password, never an exception.

    A raising verify invites a caller to conflate "wrong password" with "something broke", and
    the two must produce different responses.
    """
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """True when the stored hash used weaker parameters than the current ones."""
    return _hasher.check_needs_rehash(password_hash)
