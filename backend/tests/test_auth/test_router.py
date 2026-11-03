"""FastAPI routing and real limiter logic with mocked DB/Redis only."""

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.auth import router as auth_router
from app.config import settings
from app.core import rate_limit
from app.core.database import get_db
from asyncpg.exceptions import InvalidPasswordError
from fastapi import FastAPI
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError
from sqlalchemy.exc import SQLAlchemyError


class RouterTests(unittest.TestCase):
    def setUp(self):
        self.redis = MagicMock()
        self.redis.eval = AsyncMock(return_value=[1, 3600])
        self.redis.__aenter__ = AsyncMock(return_value=self.redis)
        self.redis.__aexit__ = AsyncMock(return_value=None)
        self.addCleanup(patch.stopall)
        patch.object(rate_limit.aioredis, "from_url", return_value=self.redis).start()
        patch.object(settings, "rate_limit_per_hour", 100).start()
        patch.object(settings, "access_token_expire_minutes", 30).start()
        self.service = patch.object(auth_router, "service").start()
        self.user = SimpleNamespace(id=uuid4(), email="auth@example.com")
        self.service.register_user = AsyncMock(return_value=self.user)
        tokens = ("access-placeholder", "refresh-placeholder", datetime.now(timezone.utc) + timedelta(days=7))
        self.service.login_user = AsyncMock(return_value=tokens)
        self.service.refresh_access_token = AsyncMock(return_value=tokens)
        self.service.logout_user = AsyncMock()
        self.db = MagicMock()
        self.db_entries = 0

        async def fake_db():
            self.db_entries += 1
            yield self.db

        app = FastAPI()
        app.include_router(auth_router.router)
        app.dependency_overrides[get_db] = fake_db
        self.client = TestClient(app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.credentials = {"email": self.user.email, "password": "unit-test-password"}
        self.refresh_body = {"refresh_token": "refresh-placeholder"}

    def test_register_endpoint_success_201(self):
        response = self.client.post("/api/v1/auth/register", json=self.credentials)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json(), {"id": str(self.user.id), "email": self.user.email})
        self.assertEqual(response.headers["cache-control"], "no-store")
        invalid = self.client.post(
            "/api/v1/auth/register", json={**self.credentials, "password": "x" * 73}
        )
        self.assertEqual(invalid.status_code, 422)
        self.assertTrue("x" * 73 not in invalid.text, "Validation must not echo secrets")
        for error in (SQLAlchemyError, InvalidPasswordError, ConnectionRefusedError):
            self.service.register_user.side_effect = error("sensitive DB parameters")
            failure = self.client.post("/api/v1/auth/register", json=self.credentials)
            self.assertEqual(failure.status_code, 503)
            self.assertNotIn("sensitive", failure.text)

    def test_register_endpoint_duplicate_400(self):
        self.service.register_user.side_effect = ValueError("Email already registered")
        response = self.client.post("/api/v1/auth/register", json=self.credentials)
        self.assertEqual(response.status_code, 400)

    def test_login_endpoint_success_200_with_tokens(self):
        response = self.client.post(
            "/api/v1/auth/login", json=self.credentials,
            headers={"user-agent": "unit-agent", "x-forwarded-for": "203.0.113.99"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()), {
            "access_token", "refresh_token", "token_type", "expires_in"
        })
        self.assertEqual(response.json()["token_type"], "bearer")
        self.assertEqual(response.json()["expires_in"], 1800)
        kwargs = self.service.login_user.call_args.kwargs
        self.assertEqual(kwargs["user_agent"], "unit-agent")
        # TestClient's peer is 'testclient', not an IP; ignore forwarded headers.
        self.assertIsNone(kwargs["ip_address"])

    def test_login_endpoint_invalid_credentials_401(self):
        self.service.login_user.side_effect = ValueError("Invalid credentials")
        response = self.client.post("/api/v1/auth/login", json=self.credentials)
        self.assertEqual(response.status_code, 401)

    def test_refresh_endpoint_success_200_new_tokens(self):
        response = self.client.post("/api/v1/auth/refresh", json=self.refresh_body)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.service.refresh_access_token.assert_awaited_once()

    def test_refresh_endpoint_invalid_token_401(self):
        self.service.refresh_access_token.side_effect = ValueError("Invalid refresh token")
        response = self.client.post("/api/v1/auth/refresh", json=self.refresh_body)
        self.assertEqual(response.status_code, 401)

    def test_refresh_endpoint_expires_in_is_1800(self):
        response = self.client.post("/api/v1/auth/refresh", json=self.refresh_body)
        self.assertEqual(response.json()["expires_in"], 1800)

    def test_logout_endpoint_204(self):
        response = self.client.post("/api/v1/auth/logout", json=self.refresh_body)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b"")
        self.service.logout_user.assert_awaited_once()

    def test_rate_limit_exceeded_429(self):
        self.redis.eval.side_effect = [[count, 3599] for count in range(1, 102)]
        for _ in range(100):
            response = self.client.post("/api/v1/auth/logout", json=self.refresh_body)
            self.assertEqual(response.status_code, 204)
        response = self.client.post("/api/v1/auth/logout", json=self.refresh_body)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["retry-after"], "3599")
        self.assertEqual(self.service.logout_user.await_count, 100)
        self.redis.eval.side_effect = None
        self.redis.eval.return_value = [101, 3600]
        for endpoint in ("register", "login", "refresh", "logout"):
            with self.subTest(endpoint=endpoint):
                response = self.client.post("/api/v1/auth/" + endpoint, content="{")
                self.assertEqual(response.status_code, 429)
                self.assertEqual(self.redis.eval.call_args.args[2], f"rate_limit:{endpoint}:testclient")
        self.assertEqual(self.db_entries, 100, "Quota must precede DB and body parsing")

    def test_rate_limit_redis_down_503(self):
        self.redis.eval.side_effect = ConnectionError("offline")
        for endpoint in ("register", "login", "refresh", "logout"):
            with self.subTest(endpoint=endpoint):
                response = self.client.post("/api/v1/auth/" + endpoint, content="{")
                self.assertEqual(response.status_code, 503)
        self.assertEqual(self.db_entries, 0)
        self.service.register_user.assert_not_awaited()
        self.service.login_user.assert_not_awaited()
        self.service.refresh_access_token.assert_not_awaited()
        self.service.logout_user.assert_not_awaited()
