"""WP2: RRF math, hybrid with fakes, three live golden queries."""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.app.retrieval.fusion import reciprocal_rank_fusion
from backend.app.retrieval.hybrid import (
    RESULT_FIELDS,
    HybridRetriever,
)

CHUNKS_SHA256 = "e6565a67c49412a2c0c31fe655bfeee41634c8ea9f5968d17d0a12fad7b6d514"


def _hit(chunk_id, source, **fields):
    payload = {name: fields.get(name) for name in RESULT_FIELDS}
    payload["chunk_id"] = chunk_id
    return {"chunk_id": chunk_id, "payload": payload, "source": source}


def _abc_lists():
    dense = [
        _hit("a", "dense", citation_id="A", act="BNS", kind="section", header="a", source_pages=[], json_pointer="/a", text="a"),
        _hit("b", "dense", citation_id="B", act="BNS", kind="section", header="b", source_pages=[], json_pointer="/b", text="b"),
        _hit("c", "dense", citation_id="C", act="BNS", kind="section", header="c", source_pages=[], json_pointer="/c", text="c"),
    ]
    sparse = [
        _hit("c", "sparse", citation_id="C", act="BNS", kind="section", header="c", source_pages=[], json_pointer="/c", text="c"),
        _hit("a", "sparse", citation_id="A", act="BNS", kind="section", header="a", source_pages=[], json_pointer="/a", text="a"),
        _hit("d", "sparse", citation_id="D", act="BNS", kind="section", header="d", source_pages=[], json_pointer="/d", text="d"),
    ]
    return dense, sparse


class RRFTests(unittest.TestCase):
    def test_order_and_scores(self):
        dense, sparse = _abc_lists()
        ranking = reciprocal_rank_fusion([dense, sparse], top_k=10)
        ids = [row["chunk_id"] for row in ranking]
        self.assertEqual(ids, ["a", "c", "b", "d"])
        by_id = {row["chunk_id"]: row for row in ranking}
        self.assertAlmostEqual(by_id["a"]["rrf_score"], 1 / 61 + 1 / 62, places=12)
        self.assertAlmostEqual(by_id["c"]["rrf_score"], 1 / 63 + 1 / 61, places=12)
        self.assertAlmostEqual(by_id["b"]["rrf_score"], 1 / 62, places=12)
        self.assertAlmostEqual(by_id["d"]["rrf_score"], 1 / 63, places=12)
        self.assertEqual(by_id["a"]["sources"], ["dense", "sparse"])
        self.assertEqual(by_id["c"]["sources"], ["dense", "sparse"])
        self.assertEqual(by_id["d"]["sources"], ["sparse"])

    def test_top_k_two(self):
        dense, sparse = _abc_lists()
        ranking = reciprocal_rank_fusion([dense, sparse], top_k=2)
        self.assertEqual([row["chunk_id"] for row in ranking], ["a", "c"])

    def test_tie_breaks_on_chunk_id(self):
        dense = [_hit("b", "dense", citation_id="B", act="BNS", kind="section", header="b", source_pages=[], json_pointer="/b", text="b")]
        sparse = [_hit("a", "sparse", citation_id="A", act="BNS", kind="section", header="a", source_pages=[], json_pointer="/a", text="a")]
        ranking = reciprocal_rank_fusion([dense, sparse], top_k=2)
        self.assertEqual([row["chunk_id"] for row in ranking], ["a", "b"])

    def test_payload_disagreement_raises(self):
        dense = [_hit("a", "dense", citation_id="A", act="BNS", kind="section", header="a", source_pages=[], json_pointer="/a", text="a")]
        sparse = [_hit("a", "sparse", citation_id="Z", act="BNS", kind="section", header="a", source_pages=[], json_pointer="/a", text="a")]
        with self.assertRaises(ValueError):
            reciprocal_rank_fusion([dense, sparse], top_k=5)

    def test_top_k_zero_raises(self):
        dense, sparse = _abc_lists()
        with self.assertRaises(ValueError):
            reciprocal_rank_fusion([dense, sparse], top_k=0)


class FakeRetriever:
    def __init__(self, source, hits, artifacts):
        self.source = source
        self.hits = hits
        self.artifacts = artifacts
        self.chunks_sha256 = artifacts.chunks_sha256
        self.calls = []

    def search(self, query, top_k, act=None):
        self.calls.append({"query": query, "k": top_k, "act": act})
        return self.hits[:top_k]


def _field(i, name):
    if name == "act":
        return "BNS"
    if name == "kind":
        return "section"
    if name == "source_pages":
        return []
    if name == "json_pointer":
        return f"/x/{i}"
    return f"{name}-{i}"


def _synthetic_hits(source, n=30):
    hits = []
    for i in range(n):
        cid = f"chunk-{i:02d}"
        fields = {name: _field(i, name) for name in RESULT_FIELDS}
        fields["citation_id"] = f"BNS-S-{i}"
        hits.append(_hit(cid, source, **fields))
    return hits


class HybridUnitTests(unittest.TestCase):
    def setUp(self):
        self.artifacts = SimpleNamespace(chunks_sha256=CHUNKS_SHA256)
        self.dense = FakeRetriever("dense", _synthetic_hits("dense"), self.artifacts)
        self.sparse = FakeRetriever("sparse", _synthetic_hits("sparse"), self.artifacts)
        self.hybrid = HybridRetriever(self.dense, self.sparse)

    def test_top_k_five_asks_for_twenty(self):
        out = self.hybrid.search("what is theft", top_k=5)
        self.assertEqual(self.dense.calls[-1]["k"], 20)
        self.assertEqual(self.sparse.calls[-1]["k"], 20)
        self.assertEqual(len(out["results"]), 5)
        self.assertEqual(out["candidate_depth"], 20)

    def test_top_k_twenty_five_asks_for_twenty_five(self):
        out = self.hybrid.search("what is theft", top_k=25)
        self.assertEqual(self.dense.calls[-1]["k"], 25)
        self.assertEqual(self.sparse.calls[-1]["k"], 25)
        self.assertEqual(len(out["results"]), 25)

    def test_ranks_and_rrf_match_lists(self):
        out = self.hybrid.search("what is theft", top_k=5)
        dense_pos = {h["chunk_id"]: i for i, h in enumerate(self.dense.hits, start=1)}
        sparse_pos = {h["chunk_id"]: i for i, h in enumerate(self.sparse.hits, start=1)}
        for row in out["results"]:
            self.assertIn("chunk_id", row)
            d, s = row["ranks"]["dense"], row["ranks"]["sparse"]
            self.assertEqual(d, dense_pos.get(row["chunk_id"]))
            self.assertEqual(s, sparse_pos.get(row["chunk_id"]))
            expected = 0.0
            if d is not None:
                expected += 1.0 / (60 + d)
            if s is not None:
                expected += 1.0 / (60 + s)
            self.assertAlmostEqual(row["rrf_score"], expected, places=12)

    def test_act_is_normalized_to_upper(self):
        self.hybrid.search("theft", top_k=5, act="bnss")
        self.assertEqual(self.dense.calls[-1]["act"], "BNSS")
        self.assertEqual(self.sparse.calls[-1]["act"], "BNSS")

    def test_blank_query_calls_nobody(self):
        out = self.hybrid.search("   ", top_k=5)
        self.assertEqual(out["results"], [])
        self.assertEqual(self.dense.calls, [])
        self.assertEqual(self.sparse.calls, [])

    def test_mismatched_hashes_raise(self):
        other = SimpleNamespace(chunks_sha256="0" * 64)
        bad = FakeRetriever("sparse", _synthetic_hits("sparse"), other)
        with self.assertRaises(ValueError):
            HybridRetriever(self.dense, bad)

    def test_standard_builds_artifacts_once(self):
        shared = SimpleNamespace(chunks_sha256=CHUNKS_SHA256)
        created = []

        class Dummy:
            def __init__(self, *args, **kwargs):
                self.kwargs = kwargs
                self.artifacts = kwargs.get("artifacts", shared)
                self.chunks_sha256 = self.artifacts.chunks_sha256
                created.append(self)

        with patch(
            "backend.app.retrieval.hybrid.CorpusArtifacts", return_value=shared
        ) as art, patch(
            "backend.app.retrieval.hybrid.DenseRetriever", Dummy
        ), patch(
            "backend.app.retrieval.hybrid.SparseRetriever", Dummy
        ):
            HybridRetriever.standard(processed_dir="data/processed")

        self.assertEqual(art.call_count, 1)
        self.assertEqual(len(created), 2)  # one dense + one sparse
        self.assertIs(created[0].artifacts, shared)
        self.assertIs(created[1].artifacts, shared)


class GoldenQueryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get("NYAYAML_SKIP_LIVE") == "1":
            raise unittest.SkipTest("NYAYAML_SKIP_LIVE=1")
        from pathlib import Path

        root = Path(__file__).resolve().parents[3]
        if not (root / "data" / "processed" / "chunks.jsonl").exists():
            raise unittest.SkipTest("chunks.jsonl missing")
        if not (root / "data" / "bm25_index" / "current.json").exists():
            raise unittest.SkipTest("bm25 index missing")
        url = os.environ.get("QDRANT_URL", "http://localhost:6333")
        try:
            import urllib.request

            urllib.request.urlopen(url + "/collections", timeout=3).read()
        except Exception as exc:
            raise unittest.SkipTest(f"Qdrant not reachable at {url}") from exc
        from backend.app.retrieval.hybrid import HybridRetriever as HR

        cls.hybrid = HR.standard()
        cls.check = cls.hybrid.check()

    def test_corpus_pin(self):
        dense = self.check["dense"]
        self.assertEqual(dense["chunks_sha256"], CHUNKS_SHA256)
        self.assertEqual(dense["counts_by_act"], {"BNS": 578, "BNSS": 1516, "BSA": 228})

    def test_punishment_for_murder(self):
        out = self.hybrid.search("punishment for murder", top_k=10)
        ids = [r["citation_id"] for r in out["results"]]
        self.assertTrue(any(__import__("re").search(r"^BNS-2023-.*-S10[34]$", i) for i in ids), ids)

    def test_compounding_bnss_359(self):
        out = self.hybrid.search("compounding of offences", top_k=10, act="BNSS")
        self.assertTrue(all(r["act"] == "BNSS" for r in out["results"]))
        self.assertRegex(out["results"][0]["citation_id"], r"-S359(-|$)")

    def test_bsa_electronic_records(self):
        out = self.hybrid.search("admissibility of electronic records", top_k=10)
        ids = [r["citation_id"] for r in out["results"]]
        self.assertTrue(any(__import__("re").search(r"^BSA-2023-.*-S63$", i) for i in ids), ids)


if __name__ == "__main__":
    unittest.main()