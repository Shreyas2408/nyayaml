"""Mocked async persistence tests; live locking is verified separately."""

import asyncio
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from app.auth import service
from app.auth.models import RefreshSession, User
from app.core import security
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.password_hash = security.hash_password("unit-test-password")

    def setUp(self):
        self.db = MagicMock(spec=AsyncSession)
        self.user = User(
            id=uuid4(), email="auth@example.com",
            hashed_password=self.password_hash, is_active=True,
        )
        self.result = MagicMock()
        self.result.scalar_one_or_none.return_value = self.user
        self.db.execute.return_value = self.result
        self.token, self.expiry = security.create_refresh_token(self.user.id)

    def assert_rotation_statement(self, statement):
        sql = str(statement.compile(dialect=postgresql.dialect()))
        for fragment in (
            "UPDATE refresh_sessions SET", "refresh_sessions.user_id =",
            "refresh_sessions.token_hash =", "refresh_sessions.revoked IS false",
            "refresh_sessions.expires_at >", "users.is_active IS true",
            "RETURNING refresh_sessions.id",
        ):
            self.assertIn(fragment, sql)

    async def test_register_user_success(self):
        self.result.scalar_one_or_none.return_value = None
        user = await service.register_user(self.db, "AUTH@example.com", "unit-test-password")
        self.assertEqual(user.email, "auth@example.com")
        self.assertTrue(user.is_active)
        self.assertTrue(user.hashed_password.startswith("$2b$12$"))
        self.assertTrue(security.verify_password("unit-test-password", user.hashed_password))
        self.db.commit.assert_awaited_once()
        self.assertTrue(self.db.add.call_args.args[0] is user)

    async def test_register_user_duplicate_email_raises(self):
        with self.assertRaises(ValueError):
            await service.register_user(self.db, self.user.email, "unit-test-password")
        self.db.add.assert_not_called()
        # The DB unique index also protects the check/insert registration race.
        self.result.scalar_one_or_none.return_value = None
        original = Exception("unique violation")
        original.sqlstate = "23505"
        self.db.commit.side_effect = IntegrityError("INSERT", {}, original)
        with self.assertRaises(ValueError):
            await service.register_user(self.db, self.user.email, "unit-test-password")
        self.db.rollback.assert_awaited_once()

    async def test_login_user_success_returns_tokens(self):
        access, refresh, expiry = await service.login_user(
            self.db, self.user.email, "unit-test-password", "127.0.0.1", "unit-agent"
        )
        self.assertEqual(security.decode_token(access)["type"], "access")
        self.assertEqual(security.decode_token(refresh)["type"], "refresh")
        row = self.db.add.call_args.args[0]
        self.assertIsInstance(row, RefreshSession)
        self.assertEqual(row.user_id, self.user.id)
        self.assertTrue(row.token_hash == security.hash_token(refresh))
        self.assertEqual(len(row.token_hash), 64)
        self.assertEqual(row.expires_at, expiry)
        self.assertEqual((row.ip_address, row.user_agent), ("127.0.0.1", "unit-agent"))
        self.db.commit.assert_awaited_once()
        self.db.get.return_value = self.user
        resolved = await service.current_user(
            HTTPAuthorizationCredentials(scheme="Bearer", credentials=access), self.db
        )
        self.assertEqual(resolved.id, self.user.id)

    async def test_login_user_wrong_password_raises(self):
        with self.assertRaises(ValueError):
            await service.login_user(self.db, self.user.email, "incorrect-password")
        self.db.add.assert_not_called()

    async def test_login_user_nonexistent_email_raises(self):
        self.result.scalar_one_or_none.return_value = None
        with self.assertRaises(ValueError):
            await service.login_user(self.db, "absent@example.com", "unit-test-password")
        self.db.commit.assert_not_awaited()

    async def test_login_user_inactive_account_raises(self):
        self.user.is_active = False
        with self.assertRaises(ValueError):
            await service.login_user(self.db, self.user.email, "unit-test-password")
        self.db.add.assert_not_called()
        self.db.get.return_value = self.user
        with self.assertRaises(HTTPException):
            await service.current_user(
                HTTPAuthorizationCredentials(
                    scheme="Bearer", credentials=security.create_access_token(self.user.id)
                ), self.db,
            )

    async def test_refresh_access_token_success_rotates(self):
        self.result.first.return_value = SimpleNamespace(
            id=uuid4(), ip_address="127.0.0.1", user_agent="unit-agent"
        )
        access, refresh, expiry = await service.refresh_access_token(self.db, self.token)
        self.assert_rotation_statement(self.db.execute.call_args.args[0])
        self.assertTrue(refresh != self.token)
        self.assertEqual(security.decode_token(access)["sub"], str(self.user.id))
        row = self.db.add.call_args.args[0]
        self.assertTrue(row.token_hash == security.hash_token(refresh))
        self.assertEqual(row.expires_at, expiry)
        self.assertEqual((row.ip_address, row.user_agent), ("127.0.0.1", "unit-agent"))
        self.db.commit.assert_awaited_once()
        self.db.commit.side_effect = RuntimeError("simulated insert/commit failure")
        with self.assertRaises(RuntimeError):
            await service.refresh_access_token(self.db, self.token)
        self.db.rollback.assert_awaited_once()

    async def test_refresh_access_token_expired_raises(self):
        expired = security.create_token(self.user.id, "refresh", timedelta(seconds=-10))
        access = security.create_access_token(self.user.id)
        for token in (expired, access, "malformed"):
            with self.assertRaises(ValueError):
                await service.refresh_access_token(self.db, token)
        self.db.execute.assert_not_awaited()

    async def test_refresh_access_token_revoked_raises(self):
        self.result.first.return_value = None
        with self.assertRaises(ValueError):
            await service.refresh_access_token(self.db, self.token)
        self.assert_rotation_statement(self.db.execute.call_args.args[0])
        self.db.add.assert_not_called()
        self.db.commit.assert_not_awaited()

    async def test_refresh_access_token_concurrent_use_only_one_succeeds(self):
        # This fake models a DB's indivisible conditional update, not SQL locking.
        # Verify the actual statement's predicates; real Postgres races run live.
        lock = asyncio.Lock()
        revoked = False

        async def atomic_update(statement):
            nonlocal revoked
            self.assert_rotation_statement(statement)
            await asyncio.sleep(0)
            async with lock:
                result = MagicMock()
                row = None if revoked else SimpleNamespace(
                    id=uuid4(), ip_address="127.0.0.1", user_agent="concurrent-test"
                )
                revoked = True
                result.first.return_value = row
                return result

        sessions = [MagicMock(spec=AsyncSession) for _ in range(2)]
        for db in sessions:
            db.execute.side_effect = atomic_update
        results = await asyncio.gather(
            *(service.refresh_access_token(db, self.token) for db in sessions),
            return_exceptions=True,
        )
        self.assertEqual(sum(isinstance(result, tuple) for result in results), 1)
        self.assertEqual(sum(isinstance(result, ValueError) for result in results), 1)
        self.assertEqual(sum(db.commit.await_count for db in sessions), 1)
        self.assertEqual(sum(db.add.call_count for db in sessions), 1)
        with self.assertRaises(ValueError):
            await service.refresh_access_token(sessions[0], self.token)

    async def test_logout_user_revokes_token(self):
        await service.logout_user(self.db, self.token)
        sql = str(self.db.execute.call_args.args[0].compile(dialect=postgresql.dialect()))
        self.assertIn("UPDATE refresh_sessions SET", sql)
        self.assertIn("refresh_sessions.revoked IS false", sql)
        self.assertIn("refresh_sessions.token_hash =", sql)
        self.db.commit.assert_awaited_once()
        self.result.first.return_value = None
        with self.assertRaises(ValueError):
            await service.refresh_access_token(self.db, self.token)
        await service.logout_user(self.db, self.token)

    async def test_logout_user_invalid_token_silent_success(self):
        for token in ("invalid", "", security.create_access_token(self.user.id)):
            await service.logout_user(self.db, token)
        self.db.execute.assert_not_awaited()
        self.db.commit.assert_not_awaited()
