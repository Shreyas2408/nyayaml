"""Inspect declarative metadata without constructing an engine."""

import unittest
from uuid import UUID, uuid4

from app.db.base import Base
from app.persistence.models import QueryJob
from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, Integer, String, Text
from sqlalchemy.dialects import postgresql

EXPECTED_COLUMNS = {
    "id",
    "created_at",
    "query_text",
    "act",
    "top_k",
    "mode",
    "status",
    "query_type",
    "fallback_used",
    "answer",
    "citations",
    "retrieved_citation_ids",
    "citations_valid",
    "hallucination_flags",
    "disclaimers",
    "corpus_sha256",
    "latency_ms",
    "error",
    "user_id",
}
REQUIRED_COLUMNS = {"id", "created_at", "query_text", "top_k", "mode", "status"}
EXPECTED_CHECKS = {
    "ck_query_jobs_mode": "mode IN ('retrieval-only','with-llm','api')",
    "ck_query_jobs_status": "status IN ('completed','failed')",
}


class ModelTests(unittest.TestCase):
    def test_table_columns_nullability_and_committed_base_contract(self):
        table = QueryJob.__table__
        self.assertEqual(table.name, "query_jobs")
        self.assertIs(table.metadata, Base.metadata)
        self.assertEqual(set(table.columns.keys()), EXPECTED_COLUMNS)
        self.assertEqual(
            {c.name for c in table.columns if not c.nullable}, REQUIRED_COLUMNS
        )
        self.assertEqual([c.name for c in table.primary_key], ["id"])
        self.assertFalse(table.foreign_keys)

    def test_column_types_preserve_audit_fields(self):
        columns = QueryJob.__table__.c
        for name in ("id", "user_id"):
            self.assertIsInstance(columns[name].type, postgresql.UUID)
            self.assertTrue(columns[name].type.as_uuid)
        for name in (
            "citations",
            "retrieved_citation_ids",
            "hallucination_flags",
            "disclaimers",
        ):
            self.assertIsInstance(columns[name].type, postgresql.JSONB)
        for name in ("query_text", "answer", "error"):
            self.assertIsInstance(columns[name].type, Text)
        for name, length in {
            "act": 8,
            "mode": 20,
            "status": 20,
            "query_type": 40,
            "corpus_sha256": 64,
        }.items():
            self.assertIsInstance(columns[name].type, String)
            self.assertEqual(columns[name].type.length, length)
        for name in ("fallback_used", "citations_valid"):
            self.assertIsInstance(columns[name].type, Boolean)
        self.assertIsInstance(columns.top_k.type, Integer)
        self.assertIsInstance(columns.latency_ms.type, Float)
        self.assertIsInstance(columns.created_at.type, DateTime)
        self.assertTrue(columns.created_at.type.timezone)

    def test_uuid_and_server_timestamp_defaults(self):
        columns = QueryJob.__table__.c
        self.assertIs(columns.id.default.arg.__wrapped__, uuid4)
        first = columns.id.default.arg(None)
        self.assertIsInstance(first, UUID)
        self.assertEqual(first.version, 4)
        self.assertNotEqual(first, columns.id.default.arg(None))
        self.assertEqual(str(columns.created_at.server_default.arg), "now()")
        self.assertIsNone(columns.status.default)

    def test_both_checks_and_both_indexes(self):
        table = QueryJob.__table__
        self.assertEqual(
            {
                c.name: str(c.sqltext)
                for c in table.constraints
                if isinstance(c, CheckConstraint)
            },
            EXPECTED_CHECKS,
        )
        self.assertEqual(
            {i.name: tuple(i.columns.keys()) for i in table.indexes},
            {
                "ix_query_jobs_created_at": ("created_at",),
                "ix_query_jobs_status": ("status",),
            },
        )
