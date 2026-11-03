"""Async repository and smoke behavior with no engine imports or network I/O."""

import inspect
import io
import json
import sys
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.evaluation.scorer import DISCLAIMERS
from app.persistence import repository
from app.persistence.models import QueryJob
from app.query.schemas import Citation, QueryRequest, QueryResponse
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession


class RepositoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.session = MagicMock(spec=AsyncSession)
        self.session.__aenter__.return_value = self.session
        self.session.__aexit__.return_value = False
        self.factory = MagicMock(return_value=self.session)
        self.engine = SimpleNamespace(dispose=AsyncMock())
        self.core = SimpleNamespace(
            async_session_maker=self.factory, engine=self.engine
        )
        self.module_patch = patch.dict(sys.modules, {"app.core.database": self.core})
        self.module_patch.start()
        self.addCleanup(self.module_patch.stop)
        self.request = QueryRequest(query="synthetic question", act="BNS", top_k=2)

        async def flush():
            self.job.id = uuid4()
            self.job.created_at = datetime.now(timezone.utc)

        self.session.flush.side_effect = flush

    @property
    def job(self):
        return self.session.add.call_args.args[0]

    async def test_full_status_matrix_and_explicit_error_precedence(self):
        cases = [
            (QueryResponse(), None, "completed", None),
            (QueryResponse(error=""), None, "completed", ""),
            (None, "retrieval failed", "failed", "retrieval failed"),
            (QueryResponse(error="model failed"), None, "failed", "model failed"),
            (QueryResponse(), "timeout", "failed", "timeout"),
            (
                QueryResponse(error="model failed"),
                "caller failed",
                "failed",
                "caller failed",
            ),
        ]
        for response, error, status, expected_error in cases:
            with self.subTest(response=response, error=error):
                self.session.reset_mock()
                job_id = await repository.record_query_job(
                    self.factory,
                    request=self.request,
                    response=response,
                    mode="api",
                    error=error,
                )
                self.assertEqual(
                    (self.job.status, self.job.error), (status, expected_error)
                )
                self.assertEqual(job_id, self.job.id)
                self.session.commit.assert_awaited_once_with()
                self.session.rollback.assert_not_awaited()
                if response is None:
                    for field in (
                        "answer",
                        "query_type",
                        "fallback_used",
                        "citations",
                        "citations_valid",
                        "retrieved_citation_ids",
                        "hallucination_flags",
                        "disclaimers",
                    ):
                        self.assertIsNone(getattr(self.job, field))

    async def test_failed_attempt_requires_nonempty_error_before_session(self):
        for response, error in [
            (None, None),
            (None, ""),
            (None, " "),
            (QueryResponse(), ""),
            (QueryResponse(error=" "), None),
            (QueryResponse(error="failed"), ""),
        ]:
            with (
                self.subTest(response=response, error=error),
                self.assertRaises(ValueError),
            ):
                await repository.record_query_job(
                    self.factory,
                    request=self.request,
                    response=response,
                    mode="api",
                    error=error,
                )
        self.factory.assert_not_called()

    async def test_unknown_mode_rejected_before_session(self):
        for mode in ("", "unknown", "API"):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                await repository.record_query_job(
                    self.factory,
                    request=self.request,
                    response=QueryResponse(),
                    mode=mode,
                )
        self.factory.assert_not_called()

    async def test_fields_and_citations_are_json_snapshots_with_one_commit(self):
        citation = Citation(
            citation_id="synthetic-id", act="BNS", header="heading", chunk_id="chunk"
        )
        response = QueryResponse(
            query="different",
            act="BSA",
            top_k=9,
            answer="answer",
            query_type="direct_lookup",
            fallback_used=True,
            citations=[citation],
            citations_valid=False,
            retrieved_citation_ids=["synthetic-id"],
            hallucination_flags=["synthetic-flag"],
            disclaimers=DISCLAIMERS,
        )
        with patch.object(
            Citation,
            "model_dump",
            autospec=True,
            return_value=citation.model_dump(mode="json"),
        ) as dump:
            await repository.record_query_job(
                self.factory,
                request=self.request,
                response=response,
                mode="with-llm",
                latency_ms=12.5,
                corpus_sha256="a" * 64,
            )
            dump.assert_called_once_with(citation, mode="json")
        self.assertEqual(
            (self.job.query_text, self.job.act, self.job.top_k),
            ("synthetic question", "BNS", 2),
        )
        for field in (
            "answer",
            "query_type",
            "fallback_used",
            "retrieved_citation_ids",
            "citations_valid",
            "hallucination_flags",
            "disclaimers",
        ):
            self.assertEqual(getattr(self.job, field), getattr(response, field))
        self.assertEqual(self.job.citations, json.loads(json.dumps(self.job.citations)))
        self.assertEqual(self.job.citations, [citation.model_dump(mode="json")])
        self.assertEqual(
            (self.job.latency_ms, self.job.corpus_sha256), (12.5, "a" * 64)
        )
        self.assertIsNone(self.job.user_id)
        response.disclaimers.append("later mutation")
        response.retrieved_citation_ids.clear()
        self.assertEqual(self.job.disclaimers, DISCLAIMERS)
        self.assertEqual(self.job.retrieved_citation_ids, ["synthetic-id"])
        self.session.commit.assert_awaited_once_with()
        self.session.rollback.assert_not_awaited()

    async def test_add_and_flush_failure_rollback_without_commit(self):
        for method in ("add", "flush"):
            with self.subTest(method=method):
                self.session.reset_mock()
                with (
                    patch.object(
                        self.session,
                        method,
                        side_effect=RuntimeError("synthetic failure"),
                    ),
                    self.assertRaisesRegex(RuntimeError, "synthetic failure"),
                ):
                    await repository.record_query_job(
                        self.factory,
                        request=self.request,
                        response=QueryResponse(),
                        mode="api",
                    )
                self.session.rollback.assert_awaited_once_with()
                self.session.commit.assert_not_awaited()
                self.session.__aexit__.assert_awaited_once()

    async def test_commit_failure_rolls_back_and_does_not_retry(self):
        self.session.commit.side_effect = RuntimeError("commit failure")
        with self.assertRaisesRegex(RuntimeError, "commit failure"):
            await repository.record_query_job(
                self.factory, request=self.request, response=QueryResponse(), mode="api"
            )
        self.session.commit.assert_awaited_once_with()
        self.session.rollback.assert_awaited_once_with()

    async def test_default_resolves_committed_sessionmaker_lazily(self):
        await repository.record_query_job(
            request=self.request, response=QueryResponse(), mode="retrieval-only"
        )
        self.factory.assert_called_once_with()
        self.session.commit.assert_awaited_once_with()

    async def test_get_uses_primary_key_and_returns_model_or_none(self):
        identifier = uuid4()
        for result in (QueryJob(id=identifier), None):
            self.session.get.return_value = result
            self.assertIs(await repository.get_query_job(identifier), result)
        self.session.get.assert_awaited_with(QueryJob, identifier)
        self.session.commit.assert_not_awaited()

    async def test_list_orders_newest_first_and_applies_limit_without_writes(self):
        rows = [QueryJob(id=uuid4())]
        self.session.scalars.return_value = SimpleNamespace(all=lambda: rows)
        for limit in (50, 3):
            result = (
                await repository.list_recent()
                if limit == 50
                else await repository.list_recent(limit)
            )
            self.assertEqual(result, rows)
            statement = self.session.scalars.call_args.args[0]
            sql = str(
                statement.compile(
                    dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
                )
            )
            self.assertIn("FROM query_jobs", sql)
            self.assertIn(
                "ORDER BY query_jobs.created_at DESC, query_jobs.id DESC", sql
            )
            self.assertIn(f"LIMIT {limit}", sql)
        with self.assertRaises(ValueError):
            await repository.list_recent(0)
        self.session.commit.assert_not_awaited()
        self.session.add.assert_not_called()

    async def test_api_has_only_record_as_mutator_and_exact_record_signature(self):
        functions = {
            n: f
            for n, f in vars(repository).items()
            if inspect.isfunction(f) and f.__module__ == repository.__name__
        }
        self.assertFalse(any("delete" in n or "update" in n for n in functions))
        self.assertEqual(
            {n for n in functions if not n.startswith("_")},
            {"record_query_job", "get_query_job", "list_recent", "main"},
        )
        signature = inspect.signature(repository.record_query_job)
        self.assertEqual(
            list(signature.parameters),
            [
                "session_factory",
                "request",
                "response",
                "mode",
                "latency_ms",
                "error",
                "corpus_sha256",
                "user_id",
            ],
        )
        self.assertIsNone(signature.parameters["session_factory"].default)
        for name in (
            "request",
            "response",
            "mode",
            "latency_ms",
            "error",
            "corpus_sha256",
            "user_id",
        ):
            self.assertEqual(
                signature.parameters[name].kind, inspect.Parameter.KEYWORD_ONLY
            )

    async def test_smoke_real_record_path_roundtrip_and_exact_row_cleanup(self):
        self.session.get.side_effect = lambda *_: (
            self.job if self.session.execute.await_count == 0 else None
        )
        with patch.object(
            repository.Path,
            "read_text",
            return_value=json.dumps({"corpus_sha256": "b" * 64}),
        ):
            result = await repository._smoke()
        self.assertEqual(
            set(result),
            {
                "id",
                "query_text",
                "status",
                "mode",
                "created_at",
                "disclaimers",
                "corpus_sha256",
                "cleanup",
            },
        )
        self.assertEqual(result["query_text"], "P34-04 smoke — not a legal query")
        self.assertEqual(
            (result["status"], result["mode"], result["cleanup"]),
            ("completed", "api", "ok"),
        )
        self.assertEqual(result["disclaimers"], DISCLAIMERS)
        self.assertEqual(result["corpus_sha256"], "b" * 64)
        self.assertEqual(
            self.session.commit.await_count, 2
        )  # record, then smoke-only cleanup
        deletion = self.session.execute.call_args.args[0].compile(
            dialect=postgresql.dialect()
        )
        self.assertIn("DELETE FROM query_jobs WHERE query_jobs.id =", str(deletion))
        self.assertEqual(list(deletion.params.values()), [self.job.id])
        self.engine.dispose.assert_awaited_once_with()

    async def test_smoke_roundtrip_failure_still_cleans_its_row(self):
        self.session.get.return_value = None
        with (
            patch.object(
                repository.Path, "read_text", side_effect=OSError("unreadable")
            ),
            self.assertRaisesRegex(ValueError, "round-trip mismatch"),
        ):
            await repository._smoke()
        self.session.execute.assert_awaited_once()
        self.assertIsNone(self.job.corpus_sha256)
        self.engine.dispose.assert_awaited_once_with()


class CliTests(unittest.TestCase):
    def test_failure_exits_two_without_leaking_exception_details(self):
        with (
            patch.object(
                repository,
                "_smoke",
                AsyncMock(side_effect=ConnectionError("secret-marker")),
            ),
            patch("sys.stderr", new_callable=io.StringIO) as stderr,
            patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            self.assertEqual(repository.main(["--smoke"]), 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("ConnectionError", stderr.getvalue())
        self.assertNotIn("secret-marker", stderr.getvalue())
