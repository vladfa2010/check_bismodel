"""Пароли (PBKDF2, stdlib) и подпись сессионных кук."""
import hashlib
import hmac
import os

from itsdangerous import URLSafeSerializer

from . import config

ITERATIONS = 200_000

signer = URLSafeSerializer(config.SESSION_SECRET, salt="fm-session")


def hash_password(password: str) -> str:
    salt = os.urandom(16).hex()
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), ITERATIONS)
    return f"pbkdf2${ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt, expected = stored.split("$")
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt), int(iterations)
        )
        return hmac.compare_digest(digest.hex(), expected)
    except (ValueError, AttributeError):
        return False
