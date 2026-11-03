"""Hand-computed summaries and HTTP ownership with mocked infrastructure."""

import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

with patch.dict(os.environ, {
    "SECRET_KEY": "offline-analytics-test-key-not-for-deployment-" * 2,
    "DATABASE_URL": "postgresql+asyncpg://unused:unused@localhost/unused",
    "REDIS_URL": "redis://localhost:6379/15",
}):
    from app.config import settings  # noqa: F401

from app.analytics import router, service
from app.core import database, rate_limit, security
from app.core.database import get_db
from app.db.models import User
from app.persistence.repository import UsageRow

NOW = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)
EMPTY = {
    "total_queries": 0, "by_status": {}, "queries_last_24h": 0,
    "avg_latency_ms": None, "p50_latency_ms": None,
    "first_query_at": None, "last_query_at": None,
}


class SummaryTests(unittest.TestCase):
    def setUp(self):
        clock = patch.object(service, "datetime")
        clock.start().now.return_value = NOW
        self.addCleanup(clock.stop)

    def test_empty_rows_have_exact_zeros_and_nulls(self):
        self.assertEqual(service.compute_summary([]), EMPTY)

    def test_hand_computed_mean_and_r7_median(self):
        rows = [UsageRow("completed", latency, NOW) for latency in (40, 10, 30, 20)]
        snapshot = list(rows)
        result = service.compute_summary(rows)
        self.assertEqual(result, {
            "total_queries": 4, "by_status": {"completed": 4}, "queries_last_24h": 4,
            "avg_latency_ms": 25.0, "p50_latency_ms": 25.0,
            "first_query_at": NOW.isoformat(), "last_query_at": NOW.isoformat(),
        })
        self.assertEqual(rows, snapshot)

    def test_null_latencies_excluded_and_zero_retained(self):
        rows = [UsageRow("failed", value, NOW) for value in (None, 0, 10, None, 20)]
        result = service.compute_summary(rows)
        self.assertEqual(result["total_queries"], 5)
        self.assertEqual(result["by_status"], {"failed": 5})
        self.assertEqual(result["avg_latency_ms"], 10.0)
        self.assertEqual(result["p50_latency_ms"], 10.0)

    def test_all_null_latencies_still_have_timestamps(self):
        result = service.compute_summary([UsageRow("failed", None, NOW)])
        self.assertIsNone(result["avg_latency_ms"])
        self.assertIsNone(result["p50_latency_ms"])
        self.assertEqual(result["first_query_at"], NOW.isoformat())

    def test_last_24h_includes_boundary_excludes_old_and_future(self):
        cutoff = NOW - timedelta(hours=24)
        rows = [UsageRow("completed", 1, instant) for instant in (
            NOW, cutoff, cutoff - timedelta(microseconds=1), NOW + timedelta(seconds=1),
        )]
        self.assertEqual(service.compute_summary(rows)["queries_last_24h"], 2)

    def test_status_counts_and_first_last_are_from_actual_rows(self):
        old = NOW - timedelta(days=2)
        rows = [UsageRow("completed", 10, NOW), UsageRow("failed", 20, old)]
        result = service.compute_summary(rows)
        self.assertEqual(result["by_status"], {"completed": 1, "failed": 1})
        self.assertEqual(result["first_query_at"], old.isoformat())
        self.assertEqual(result["last_query_at"], NOW.isoformat())
        self.assertEqual(result["queries_last_24h"], 1)


class AnalyticsRouterTests(unittest.TestCase):
    def setUp(self):
        self.user = User(id=uuid4(), email="analytics@example.com", is_active=True)
        self.other = User(id=uuid4(), email="other@example.com", is_active=True)
        self.users = {self.user.id: self.user, self.other.id: self.other}
        self.rows = []
        self.statements = []
        self.sessions = []
        self.auth_db = MagicMock()
        self.auth_db.get = AsyncMock(side_effect=lambda model, uid: self.users.get(uid))

        def session_factory():
            session = MagicMock(spec=AsyncSession)
            session.__aenter__.return_value = session
            session.__aexit__.return_value = False

            async def execute(statement):
                self.statements.append(statement)
                compiled = statement.compile(dialect=postgresql.dialect())
                selected = list(self.rows)
                # Missing the SQL predicate exposes both owners and fails the test.
                if "WHERE query_jobs.user_id =" in str(compiled):
                    selected = [row for row in selected if row.user_id == compiled.params["user_id_1"]]
                return SimpleNamespace(all=lambda: selected)

            session.execute.side_effect = execute
            self.sessions.append(session)
            return session

        self.redis = MagicMock()
        self.redis.__aenter__ = AsyncMock(return_value=self.redis)
        self.redis.__aexit__ = AsyncMock(return_value=None)
        self.counters = {}

        async def consume(script, count, key, seconds):
            self.counters[key] = self.counters.get(key, 0) + 1
            return [self.counters[key], seconds]

        self.redis.eval = AsyncMock(side_effect=consume)
        for patcher in (
            patch.object(rate_limit.aioredis, "from_url", return_value=self.redis),
            patch.object(database, "async_session_maker", side_effect=session_factory),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        app = FastAPI()
        app.include_router(router.router)
        app.dependency_overrides[get_db] = lambda: self.auth_db
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.headers = {"Authorization": "Bearer " + security.create_access_token(self.user.id)}
        self.other_headers = {"Authorization": "Bearer " + security.create_access_token(self.other.id)}

    def get(self, headers=None, suffix=""):
        return self.client.get(
            "/api/v1/analytics/summary" + suffix,
            headers=self.headers if headers is None else headers,
        )

    def add_row(self, user, latency=10, status="completed"):
        self.rows.append(SimpleNamespace(
            user_id=user.id, status=status, latency_ms=latency,
            created_at=datetime.now(timezone.utc), query_text="private legal text",
        ))

    def test_requires_valid_access_token(self):
        refresh, _ = security.create_refresh_token(self.user.id)
        expired = security.create_token(self.user.id, "access", timedelta(seconds=-10))
        for token in (None, "invalid", expired, refresh):
            headers = {} if token is None else {"Authorization": "Bearer " + token}
            self.assertEqual(self.get(headers).status_code, 401)
        self.redis.eval.assert_not_awaited()
        self.assertEqual(self.sessions, [])

    def test_fresh_user_exact_empty_summary_and_no_store(self):
        response = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), EMPTY)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_summary_uses_only_callers_measurements(self):
        self.add_row(self.user, 10)
        self.add_row(self.user, 30, "failed")
        self.add_row(self.other, 9999)
        response = self.get()
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["total_queries"], 2)
        self.assertEqual(result["by_status"], {"completed": 1, "failed": 1})
        self.assertEqual(result["avg_latency_ms"], 20.0)
        self.assertEqual(result["p50_latency_ms"], 20.0)
        self.assertEqual(set(result), set(EMPTY))
        self.assertNotIn("private legal text", response.text)
        self.assertNotIn(self.user.email, response.text)

    def test_second_user_cannot_see_first_users_queries(self):
        self.add_row(self.user)
        self.assertEqual(self.get().json()["total_queries"], 1)
        self.assertEqual(self.get(self.other_headers).json(), EMPTY)

    def test_user_id_and_admin_parameters_cannot_override_owner(self):
        self.add_row(self.other)
        response = self.get(suffix=f"?user_id={self.other.id}&admin=true")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), EMPTY)
        compiled = self.statements[-1].compile(dialect=postgresql.dialect())
        self.assertEqual(compiled.params["user_id_1"], self.user.id)

    def test_quota_120_per_owner_then_429(self):
        for _ in range(120):
            self.assertEqual(self.get().status_code, 200)
        response = self.get()
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["retry-after"], "3600")
        self.assertEqual(len(self.statements), 120)
        self.assertEqual(self.get(self.other_headers).status_code, 200)
        self.assertIn(f"rate_limit:analytics:{self.user.id}", self.counters)

    def test_redis_down_fails_closed_before_usage_read(self):
        self.redis.eval.side_effect = ConnectionError("private redis configuration")
        response = self.get()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"detail": "Rate limiting unavailable"})
        self.assertEqual(self.sessions, [])

    def test_usage_database_failure_is_redacted_503(self):
        with patch.object(router, "list_usage_for_analytics", side_effect=SQLAlchemyError("private")):
            response = self.get()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"detail": "Usage storage unavailable"})

    def test_reads_use_fresh_sessions_without_writes(self):
        self.get()
        self.get()
        self.assertEqual(len(self.sessions), 2)
        self.assertIsNot(self.sessions[0], self.sessions[1])
        for session in self.sessions:
            session.__aexit__.assert_awaited_once()
            session.commit.assert_not_awaited()
            session.add.assert_not_called()

    def test_only_summary_route_exists(self):
        self.assertEqual(
            [(route.path, route.methods) for route in router.router.routes],
            [("/api/v1/analytics/summary", {"GET"})],
        )
