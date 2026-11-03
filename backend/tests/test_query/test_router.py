"""Offline query API contract tests with mocked DB, Redis and orchestration."""

import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

# Import configuration with test-only values before infrastructure is loaded.
with patch.dict(os.environ, {
    "SECRET_KEY": "query-api-offline-test-key-not-for-deployment-" * 2,
    "DATABASE_URL": "postgresql+asyncpg://unused:unused@localhost/unused",
    "REDIS_URL": "redis://localhost:6379/15",
    "ACCESS_TOKEN_EXPIRE_MINUTES": "30",
    "REFRESH_TOKEN_EXPIRE_DAYS": "7",
    "RATE_LIMIT_PER_HOUR": "100",
}):
    from app.config import settings

from app.core import rate_limit, security
from app.core.database import get_db
from app.db.models import User
from app.persistence.models import QueryJob
from app.query import router as query_router
from app.query.dependencies import get_query_runner, get_session_factory
from app.query.schemas import Citation, QueryResponse
from fastapi import FastAPI
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession


class MockStore:
    """Sessions hold pending rows until commit; reads interpret SQL predicates."""

    def __init__(self):
        self.rows = []
        self.users = {}
        self.sessions = []
        self.statements = []
        self.fail_commit = False
        self.clock = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def session(self):
        session = MagicMock(spec=AsyncSession)
        session.__aenter__.return_value = session
        session.__aexit__.return_value = False
        pending = []
        session.add.side_effect = pending.append
        session.get.side_effect = lambda model, identifier: self.users.get(identifier)

        async def flush():
            for job in pending:
                job.id = uuid4()
                self.clock += timedelta(seconds=1)
                job.created_at = self.clock

        async def commit():
            if self.fail_commit:
                raise RuntimeError("private audit parameters")
            self.rows.extend(pending)
            pending.clear()

        async def rollback():
            pending.clear()

        async def scalars(statement):
            self.statements.append(statement)
            compiled = statement.compile(dialect=postgresql.dialect())
            sql = str(compiled)
            rows = list(self.rows)
            # Omitting the repository's WHERE really does expose every row here,
            # so isolation assertions would catch that regression.
            if "WHERE query_jobs.user_id =" in sql:
                rows = [row for row in rows if row.user_id == compiled.params["user_id_1"]]
            if "ORDER BY query_jobs.created_at DESC, query_jobs.id DESC" in sql:
                rows.sort(key=lambda row: (row.created_at, row.id), reverse=True)
            if "LIMIT" in sql:
                rows = rows[:compiled.params["param_1"]]
            return SimpleNamespace(all=lambda: rows)

        session.flush.side_effect = flush
        session.commit.side_effect = commit
        session.rollback.side_effect = rollback
        session.scalars.side_effect = scalars
        self.sessions.append(session)
        return session


class QueryRouterTests(unittest.TestCase):
    def setUp(self):
        self.store = MockStore()
        self.user1 = User(id=uuid4(), email="one@example.com", is_active=True)
        self.user2 = User(id=uuid4(), email="two@example.com", is_active=True)
        self.store.users = {self.user1.id: self.user1, self.user2.id: self.user2}
        self.headers1 = {"Authorization": "Bearer " + security.create_access_token(self.user1.id)}
        self.headers2 = {"Authorization": "Bearer " + security.create_access_token(self.user2.id)}
        self.request_body = {"query": "private synthetic question", "act": "BNS", "top_k": 5}
        self.answer = QueryResponse(
            query=self.request_body["query"], act="BNS", top_k=5,
            answer="private synthetic answer", query_type="direct_lookup",
            citations=[Citation(citation_id="synthetic-citation", act="BNS")],
            retrieved_citation_ids=["synthetic-citation"],
        )
        self.runner = MagicMock(return_value=self.answer)
        self.redis = MagicMock()
        self.redis.__aenter__ = AsyncMock(return_value=self.redis)
        self.redis.__aexit__ = AsyncMock(return_value=None)
        self.counters = {}

        async def increment(script, count, key, seconds):
            self.counters[key] = self.counters.get(key, 0) + 1
            return [self.counters[key], seconds]

        self.redis.eval = AsyncMock(side_effect=increment)
        client_patch = patch.object(rate_limit.aioredis, "from_url", return_value=self.redis)
        client_patch.start()
        self.addCleanup(client_patch.stop)
        limit_patch = patch.object(settings, "rate_limit_per_hour", 100)
        limit_patch.start()
        self.addCleanup(limit_patch.stop)

        async def auth_db():
            async with self.store.session() as session:
                yield session

        app = FastAPI()
        app.include_router(query_router.router, prefix="/api/v1/query")
        app.dependency_overrides[get_db] = auth_db
        app.dependency_overrides[get_session_factory] = lambda: self.store.session
        app.dependency_overrides[get_query_runner] = lambda: self.runner
        self.client = TestClient(app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def post(self, headers=None, **overrides):
        return self.client.post(
            "/api/v1/query", json={**self.request_body, **overrides},
            headers=self.headers1 if headers is None else headers,
        )

    def history(self, headers=None, suffix=""):
        return self.client.get(
            "/api/v1/query/history" + suffix,
            headers=self.headers1 if headers is None else headers,
        )

    def test_query_endpoint_requires_authentication_401(self):
        refresh, _ = security.create_refresh_token(self.user1.id)
        expired = security.create_token(self.user1.id, "access", timedelta(seconds=-10))
        for token in (None, "malformed", expired, refresh):
            headers = {} if token is None else {"Authorization": "Bearer " + token}
            self.assertEqual(self.post(headers).status_code, 401)
        self.user1.is_active = False
        self.assertEqual(self.post().status_code, 401)
        self.runner.assert_not_called()
        self.redis.eval.assert_not_awaited()
        self.assertEqual(len(self.store.rows), 0)

    def test_query_endpoint_success_200(self):
        with self.assertLogs(query_router.logger, level="INFO") as logs:
            response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json() == self.answer.model_dump(mode="json"))
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(UUID(response.headers["x-query-id"]), self.store.rows[0].id)
        self.assertEqual(self.runner.call_count, 1)
        self.assertTrue(all(self.request_body["query"] not in line for line in logs.output))
        self.assertTrue(all(self.answer.answer not in line for line in logs.output))

    def test_query_endpoint_records_in_audit_trail(self):
        response = self.post(user_id=str(self.user2.id))
        self.assertEqual(response.status_code, 200)
        job = self.store.rows[0]
        self.assertIsInstance(job, QueryJob)
        self.assertEqual((job.user_id, job.mode, job.status), (self.user1.id, "api", "completed"))
        self.assertTrue(job.query_text == self.request_body["query"])
        self.assertTrue(job.answer == self.answer.answer)
        self.assertEqual(job.citations, [citation.model_dump(mode="json") for citation in self.answer.citations])
        self.assertEqual(job.disclaimers, self.answer.disclaimers)
        self.assertGreaterEqual(job.latency_ms, 0)
        self.assertEqual(sum(session.commit.await_count for session in self.store.sessions), 1)
        self.store.fail_commit = True
        failure = self.post()
        self.assertEqual(failure.status_code, 503)
        self.assertEqual(len(self.store.rows), 1)
        self.assertNotIn("private", failure.text)
        self.assertEqual(sum(session.rollback.await_count for session in self.store.sessions), 1)

    def test_query_endpoint_rate_limit_30_per_hour(self):
        for _ in range(30):
            self.assertEqual(self.post().status_code, 200)
        response = self.post()
        self.assertEqual(response.status_code, 429)
        self.assertEqual(self.runner.call_count, 30)
        self.assertEqual(len(self.store.rows), 30)
        self.assertEqual(response.headers["retry-after"], "3600")
        self.assertEqual(self.post(self.headers2).status_code, 200)
        self.assertEqual(self.counters["rate_limit:query:" + str(self.user1.id)], 31)
        self.assertEqual(self.counters["rate_limit:query:" + str(self.user2.id)], 1)

    def test_query_endpoint_rate_limit_fails_closed_503(self):
        self.redis.eval.side_effect = ConnectionError("private connection details")
        response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private", response.text)
        self.runner.assert_not_called()
        self.assertEqual(len(self.store.rows), 0)

    def test_query_endpoint_orchestrator_failure_recorded_500(self):
        self.runner.side_effect = RuntimeError("private query and answer")
        response = self.post()
        self.assertEqual(response.status_code, 500)
        job = self.store.rows[0]
        self.assertEqual((job.status, job.error), ("failed", "query_processing_failed"))
        self.assertEqual(job.user_id, self.user1.id)
        self.assertEqual(UUID(response.headers["x-query-id"]), job.id)
        self.assertNotIn("private", response.text)
        self.runner.side_effect = None
        for error in ("llm_unavailable", "parse_error"):
            self.runner.return_value = QueryResponse(error=error)
            self.assertEqual(self.post().status_code, 500)
            self.assertEqual((self.store.rows[-1].status, self.store.rows[-1].error), ("failed", error))
        self.store.fail_commit = True
        self.assertEqual(self.post().status_code, 503)

    def test_query_history_requires_authentication_401(self):
        self.assertEqual(self.history({}).status_code, 401)
        self.assertEqual(self.history({"Authorization": "Bearer invalid"}).status_code, 401)
        self.assertEqual(self.store.statements, [])

    def test_query_history_returns_only_own_queries(self):
        self.assertEqual(self.post().status_code, 200)
        own = self.history()
        self.assertEqual(own.status_code, 200)
        self.assertEqual([row["id"] for row in own.json()], [str(self.store.rows[0].id)])
        other = self.history(self.headers2)
        self.assertEqual(other.status_code, 200)
        self.assertEqual(other.json(), [])
        self.assertEqual(set(own.json()[0]), {
            "id", "query_text", "act", "status", "created_at", "answer", "citations", "error"
        })

    def test_query_history_limit_parameter(self):
        for _ in range(3):
            self.assertEqual(self.post().status_code, 200)
        self.assertEqual(len(self.history(suffix="?limit=2").json()), 2)
        statement = self.store.statements[-1].compile(dialect=postgresql.dialect())
        self.assertEqual(statement.params["param_1"], 2)
        self.history()
        statement = self.store.statements[-1].compile(dialect=postgresql.dialect())
        self.assertEqual(statement.params["param_1"], 50)
        for value in ("0", "-1", "101", "invalid"):
            response = self.history(suffix="?limit=" + value)
            self.assertEqual(response.status_code, 422)
        bad = self.post(top_k=100)
        self.assertEqual(bad.status_code, 422)
        self.assertNotIn("private", bad.text)

    def test_query_history_empty_for_new_user(self):
        response = self.history()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.runner.assert_not_called()

    def test_query_history_excludes_other_users_queries(self):
        self.post()
        other_id = self.store.rows[-1].id
        self.post(self.headers2)
        own_id = self.store.rows[-1].id
        response = self.history(self.headers2, suffix="?user_id=" + str(self.user1.id) + "&admin=true")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["id"] for row in response.json()], [str(own_id)])
        self.assertNotIn(str(other_id), response.text)
        statement = self.store.statements[-1].compile(dialect=postgresql.dialect())
        self.assertIn("WHERE query_jobs.user_id =", str(statement))
        self.assertEqual(statement.params["user_id_1"], self.user2.id)

    def test_query_history_ordered_newest_first(self):
        for _ in range(3):
            self.post()
        self.store.rows[0].created_at = self.store.rows[1].created_at
        expected = sorted(self.store.rows, key=lambda row: (row.created_at, row.id), reverse=True)
        response = self.history()
        self.assertEqual([row["id"] for row in response.json()], [str(row.id) for row in expected])
        self.assertIn(
            "ORDER BY query_jobs.created_at DESC, query_jobs.id DESC",
            str(self.store.statements[-1].compile(dialect=postgresql.dialect())),
        )
