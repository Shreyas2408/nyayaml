"""Verbatim corpus responses with real auth and mocked database/Redis I/O."""

import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError

with patch.dict(os.environ, {
    "SECRET_KEY": "offline-sections-test-key-not-for-deployment-" * 2,
    "DATABASE_URL": "postgresql+asyncpg://unused:unused@localhost/unused",
    "REDIS_URL": "redis://localhost:6379/15",
}):
    from app.config import settings

from app.core import rate_limit, security
from app.core.database import get_db
from app.db.models import User
from app.evaluation.scorer import DISCLAIMERS
from app.sections import router, service

CORPUS = Path(__file__).resolve().parents[3] / "data/processed/Bns_Bnss_Bsa_2023_final.json"


class SectionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.documents = json.loads(CORPUS.read_text(encoding="utf-8"))["documents"]
        cls.records = [
            (document["document_name"].split("_")[0].upper(), record)
            for document in cls.documents for record in document["sections"]
        ]

    def setUp(self):
        self.user = User(id=uuid4(), email="sections@example.com", is_active=True)
        self.other = User(id=uuid4(), email="other@example.com", is_active=True)
        self.users = {self.user.id: self.user, self.other.id: self.other}
        self.db = MagicMock()
        self.db.get = AsyncMock(side_effect=lambda model, uid: self.users.get(uid))
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
            patch.object(settings, "corpus_path", CORPUS),
            patch.object(service, "_index", None),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        app = FastAPI()
        app.include_router(router.router)
        app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.headers = {"Authorization": "Bearer " + security.create_access_token(self.user.id)}

    def get(self, suffix, headers=None):
        return self.client.get(
            "/api/v1/sections" + suffix,
            headers=self.headers if headers is None else headers,
        )

    def test_both_endpoints_require_valid_access_token(self):
        refresh, _ = security.create_refresh_token(self.user.id)
        expired = security.create_token(self.user.id, "access", timedelta(seconds=-10))
        for token in (None, "invalid", expired, refresh):
            for path in ("/acts", "/BNS/1"):
                headers = {} if token is None else {"Authorization": "Bearer " + token}
                self.assertEqual(self.get(path, headers).status_code, 401)
        self.redis.eval.assert_not_awaited()
        self.assertIsNone(service._index)

    def test_acts_counts_match_real_corpus_grouping_and_exact_keys(self):
        response = self.get("/acts")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(set(data), {"acts", "source_snapshot", "disclaimer"})
        expected = {
            d["document_name"].split("_")[0].upper(): len(d["sections"])
            for d in self.documents
        }
        self.assertEqual({a["act"]: a["section_count"] for a in data["acts"]}, expected)
        self.assertEqual(sum(a["section_count"] for a in data["acts"]), len(self.records))
        for act in data["acts"]:
            self.assertEqual(set(act), {"act", "section_count"})
        self.assertEqual(data["source_snapshot"], "2023")
        self.assertEqual(data["disclaimer"], DISCLAIMERS[1])

    def test_default_corpus_path_points_to_real_snapshot(self):
        # Check the Settings field default, not setUp's injected fixture path.
        self.assertEqual(type(settings).model_fields["corpus_path"].default, CORPUS)
        self.assertTrue(CORPUS.is_file())

    def test_first_record_byte_exact_round_trip(self):
        act, record = self.records[0]
        response = self.get(f"/{act}/{record['section_number']}")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(set(data), set(record) | {"source_snapshot", "disclaimer"})
        self.assertEqual(data.pop("source_snapshot"), "2023")
        self.assertEqual(data.pop("disclaimer"), DISCLAIMERS[1])
        self.assertEqual(data, record)
        self.assertEqual(data["text"].encode("utf-8"), record["text"].encode("utf-8"))
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_all_corpus_records_and_nested_text_are_unchanged(self):
        for act, record in self.records:
            actual = service.get_section(act, record["section_number"])
            # Canonical JSON bytes compare all nested strings, lists and keys.
            self.assertTrue(
                json.dumps(actual, ensure_ascii=False, sort_keys=True).encode("utf-8")
                == json.dumps(record, ensure_ascii=False, sort_keys=True).encode("utf-8")
            )

    def test_record_specific_optional_keys_are_preserved_over_http(self):
        seen = set()
        for act, record in self.records:
            shape = (act, frozenset(record))
            if shape in seen:
                continue
            seen.add(shape)
            response = self.get(f"/{act}/{record['section_number']}")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(set(response.json()), set(record) | {"source_snapshot", "disclaimer"})
            self.assertTrue(all(response.json()[key] == value for key, value in record.items()))

    def test_unknown_act_and_section_have_generic_404(self):
        for path in ("/UNKNOWN/1", "/BNS/999999", "/BNS/01", "/BNS/not-a-section"):
            response = self.get(path)
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.json(), {"detail": "Section not found"})

    def test_only_lookup_keys_are_normalized(self):
        expected = self.records[0][1]
        self.assertEqual(service.get_section(" bns ", " 1 "), expected)
        self.assertIsNone(service.get_section("BNS", "01"))

    def test_callers_cannot_mutate_cached_records_or_counts(self):
        record = service.get_section("BNS", "1")
        record["chapter"]["title"] = "changed"
        record["subsections"].clear()
        acts = service.list_acts()
        acts[0]["section_count"] = -1
        self.assertEqual(service.get_section("BNS", "1"), self.records[0][1])
        self.assertTrue(all(row["section_count"] > 0 for row in service.list_acts()))

    def test_concurrent_lazy_load_reads_corpus_once(self):
        with patch.object(service.json, "load", wraps=json.load) as read:
            self.assertEqual(read.call_count, 0)
            with ThreadPoolExecutor(max_workers=8) as pool:
                records = list(pool.map(lambda _: service.get_section("BNS", "1"), range(24)))
            service.list_acts()
            read.assert_called_once()
        self.assertTrue(all(record == self.records[0][1] for record in records))

    def test_shared_sections_quota_120_per_owner(self):
        for i in range(120):
            self.assertEqual(self.get("/acts" if i % 2 else "/BNS/1").status_code, 200)
        self.assertEqual(self.get("/acts").status_code, 429)
        self.assertEqual(self.get("/BNS/1").status_code, 429)
        other_headers = {"Authorization": "Bearer " + security.create_access_token(self.other.id)}
        self.assertEqual(self.get("/acts", other_headers).status_code, 200)
        self.assertIn(f"rate_limit:sections:{self.user.id}", self.counters)

    def test_redis_failure_is_503_before_corpus_load(self):
        self.redis.eval.side_effect = ConnectionError("private connection details")
        for path in ("/acts", "/BNS/1"):
            response = self.get(path)
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json(), {"detail": "Rate limiting unavailable"})
        self.assertIsNone(service._index)

    def test_unreadable_corpus_is_generic_503(self):
        with patch.object(Path, "open", side_effect=OSError("private path")):
            for path in ("/acts", "/BNS/1"):
                response = self.get(path)
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json(), {"detail": "Statutory snapshot unavailable"})
        self.assertIsNone(service._index)

    def test_duplicate_corpus_keys_fail_without_publishing_partial_cache(self):
        document = self.documents[0]
        duplicate = {**document, "sections": [document["sections"][0]] * 2}
        with patch.object(service.json, "load", return_value={"documents": [duplicate]}):
            self.assertEqual(self.get("/acts").status_code, 503)
        self.assertIsNone(service._index)

    def test_exactly_two_section_routes(self):
        self.assertEqual(
            {(route.path, frozenset(route.methods)) for route in router.router.routes},
            {("/api/v1/sections/acts", frozenset({"GET"})),
             ("/api/v1/sections/{act}/{section}", frozenset({"GET"}))},
        )
