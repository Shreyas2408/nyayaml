"""Cryptographic behavior without network services or credential output."""

import hashlib
import unittest
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import jwt
from app.config import settings
from app.core import security


class SecurityTests(unittest.TestCase):
    def test_hash_password_uses_bcrypt_cost_12(self):
        hashed = security.hash_password("unit-test-password")
        self.assertTrue(hashed.startswith("$2b$12$"))
        self.assertTrue(hashed != "unit-test-password")
        with self.assertRaises(ValueError):
            security.hash_password("a" * 73)
        with self.assertRaises(ValueError):
            security.hash_password("अ" * 25)

    def test_verify_password_correct_and_incorrect(self):
        hashed = security.hash_password("unit-test-password")
        self.assertTrue(security.verify_password("unit-test-password", hashed))
        self.assertFalse(security.verify_password("incorrect-password", hashed))
        self.assertFalse(security.verify_password("a" * 73, hashed))
        self.assertFalse(security.verify_password("unit-test-password", "corrupt"))
        self.assertFalse(security.verify_password("", hashed))

    def test_create_access_token_contains_user_id_and_expiry(self):
        user_id = uuid4()
        claims = security.decode_token(security.create_access_token(user_id))
        self.assertEqual(claims["sub"], str(user_id))
        self.assertEqual(claims["type"], "access")
        self.assertEqual(claims["exp"] - claims["iat"], 1800)
        self.assertLess(abs(claims["iat"] - datetime.now(timezone.utc).timestamp()), 2)

    def test_create_refresh_token_has_jti_and_longer_expiry(self):
        user_id = uuid4()
        token, expiry = security.create_refresh_token(user_id)
        other, _ = security.create_refresh_token(user_id)
        claims = security.decode_token(token)
        self.assertEqual(claims["sub"], str(user_id))
        self.assertEqual(claims["type"], "refresh")
        self.assertEqual(UUID(claims["jti"]).version, 4)
        self.assertEqual(claims["exp"] - claims["iat"], 7 * 86400)
        self.assertEqual(expiry.timestamp(), claims["exp"])
        self.assertTrue(token != other, "Refresh tokens must be unique")

    def test_decode_token_valid_and_expired(self):
        token = security.create_access_token(uuid4())
        claims = security.decode_token(token)
        expired = security.create_token(uuid4(), "access", timedelta(seconds=-10))
        with self.assertRaises(jwt.InvalidTokenError):
            security.decode_token(expired)
        variants = []
        for name in ("sub", "exp", "iat", "type", "jti", "iss", "aud"):
            incomplete = dict(claims)
            del incomplete[name]
            variants.append(incomplete)
        for field, value in (
            ("sub", "invalid-uuid"), ("jti", "invalid-uuid"), ("type", "other"),
            ("aud", "another-service"), ("iss", "another-issuer"), ("exp", "later"),
            ("sub", []), ("type", []), ("iat", None), ("exp", claims["iat"]),
        ):
            variants.append({**claims, field: value})
        for index, invalid in enumerate(variants):
            with self.subTest(variant=index), self.assertRaises(jwt.InvalidTokenError):
                security.decode_token(jwt.encode(invalid, settings.secret_key, algorithm="HS256"))
        for invalid in ("malformed", token + "tampered", "a" * 2049):
            with self.assertRaises(jwt.InvalidTokenError):
                security.decode_token(invalid)
        with self.assertRaises(jwt.InvalidTokenError):
            security.decode_token(jwt.encode(claims, settings.secret_key, algorithm="HS512"))

    def test_decode_token_rejects_wrong_secret(self):
        claims = security.decode_token(security.create_access_token(uuid4()))
        token = jwt.encode(claims, "different-test-key-" * 4, algorithm="HS256")
        with self.assertRaises(jwt.InvalidTokenError):
            security.decode_token(token)

    def test_hash_token_returns_sha256_hexdigest_64_chars(self):
        first = "a" * 100 + "first"
        second = "a" * 100 + "second"
        digest = security.hash_token(first)
        self.assertEqual(len(digest), 64)
        self.assertTrue(all(char in "0123456789abcdef" for char in digest))
        self.assertTrue(digest == hashlib.sha256(first.encode()).hexdigest())
        self.assertTrue(digest != security.hash_token(second), "Suffix must affect digest")

    def test_verify_token_hash_correct_and_incorrect(self):
        token, _ = security.create_refresh_token(uuid4())
        digest = security.hash_token(token)
        self.assertTrue(security.verify_token_hash(token, digest))
        self.assertFalse(security.verify_token_hash(token + "suffix", digest))
