"""Exercise handwritten migration operations using mocks only."""

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import alembic
from app.persistence.models import QueryJob
from sqlalchemy import CheckConstraint, Column
from sqlalchemy.dialects import postgresql

from .test_models import EXPECTED_CHECKS, EXPECTED_COLUMNS, REQUIRED_COLUMNS

VERSIONS = Path(__file__).resolve().parents[2] / "alembic/versions"


def load_migration(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.op = MagicMock()
        with patch.object(alembic, "op", self.op):
            self.migration = load_migration(VERSIONS / "0002_phase34.py")

    def test_revision_chains_from_real_0001(self):
        predecessor = load_migration(next(VERSIONS.glob("0001_*.py")))
        self.assertEqual(self.migration.revision, "0002")
        self.assertEqual(self.migration.down_revision, predecessor.revision)
        self.assertIsNone(self.migration.branch_labels)
        self.assertIsNone(self.migration.depends_on)

    def test_upgrade_creates_only_audit_table_and_two_indexes(self):
        self.migration.upgrade()
        self.op.create_table.assert_called_once()
        table_name, *items = self.op.create_table.call_args.args
        self.assertEqual(table_name, "query_jobs")
        columns = {item.name: item for item in items if isinstance(item, Column)}
        self.assertEqual(set(columns), EXPECTED_COLUMNS)
        self.assertEqual(
            {n for n, c in columns.items() if not c.nullable}, REQUIRED_COLUMNS
        )
        for name, column in columns.items():
            model_column = QueryJob.__table__.c[name]
            self.assertEqual(
                str(column.type.compile(dialect=postgresql.dialect())),
                str(model_column.type.compile(dialect=postgresql.dialect())),
            )
            self.assertEqual(column.primary_key, model_column.primary_key)
            self.assertFalse(column.foreign_keys)
        self.assertEqual(str(columns["created_at"].server_default.arg), "now()")
        self.assertEqual(
            {c.name: str(c.sqltext) for c in items if isinstance(c, CheckConstraint)},
            EXPECTED_CHECKS,
        )
        self.assertEqual(
            self.op.create_index.call_args_list,
            [
                call("ix_query_jobs_created_at", "query_jobs", ["created_at"]),
                call("ix_query_jobs_status", "query_jobs", ["status"]),
            ],
        )
        self.assertEqual(
            [c[0] for c in self.op.mock_calls],
            ["create_table", "create_index", "create_index"],
        )

    def test_downgrade_drops_only_query_jobs(self):
        self.migration.downgrade()
        self.assertEqual(self.op.mock_calls, [call.drop_table("query_jobs")])
