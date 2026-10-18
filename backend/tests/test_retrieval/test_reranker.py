"""WP5a: pure reranker unit tests (Dummy encoder only, no MiniLM / Qdrant)."""

from __future__ import annotations

import unittest

from backend.app.retrieval.reranker import Reranker


class DummyEncoder:
    def __init__(self, scores):
        self.scores = list(scores)
        self.calls = 0
        self.last_pairs = None

    def predict(self, pairs):
        self.calls += 1
        self.last_pairs = list(pairs)
        # Allow dynamic length for the fusion_cut test
        if len(self.scores) == len(pairs):
            return list(self.scores)
        return [0.0] * len(pairs)


def _fused_hit(chunk_id, text, rrf_score=0.01, dense_rank=1, sparse_rank=1, **extra):
    """Flat hit shape matching hybrid._response output."""
    hit = {
        "rank": dense_rank,  # old RRF rank; reranker must overwrite
        "chunk_id": chunk_id,
        "rrf_score": rrf_score,
        "sources": ["dense", "sparse"],
        "ranks": {"dense": dense_rank, "sparse": sparse_rank},
        "citation_id": f"BNSS-2023-test-{chunk_id}",
        "act": "BNSS",
        "kind": "section",
        "header": f"header-{chunk_id}",
        "source_pages": [1],
        "json_pointer": f"/x/{chunk_id}",
        "text": text,
    }
    hit.update(extra)
    return hit


class RerankerUnitTests(unittest.TestCase):
    def test_empty_query_returns_empty_and_skips_encoder(self):
        dummy = DummyEncoder([0.5, 0.4, 0.3])
        reranker = Reranker(encoder=dummy)
        hits = [
            _fused_hit("a", "passage a"),
            _fused_hit("b", "passage b"),
        ]
        out = reranker.rerank("   ", hits, top_k=3)
        self.assertEqual(out, [])
        self.assertEqual(dummy.calls, 0)

    def test_empty_hits_returns_empty_and_skips_encoder(self):
        dummy = DummyEncoder([0.5])
        reranker = Reranker(encoder=dummy)
        out = reranker.rerank("compounding", [], top_k=3)
        self.assertEqual(out, [])
        self.assertEqual(dummy.calls, 0)

    def test_score_order(self):
        # scores [0.1, 0.9, 0.3] → order should be index 1, 2, 0
        dummy = DummyEncoder([0.1, 0.9, 0.3])
        reranker = Reranker(encoder=dummy)
        hits = [
            _fused_hit("a", "text-a", rrf_score=0.03),
            _fused_hit("b", "text-b", rrf_score=0.02),
            _fused_hit("c", "text-c", rrf_score=0.01),
        ]
        out = reranker.rerank("query", hits, top_k=3)
        self.assertEqual([h["chunk_id"] for h in out], ["b", "c", "a"])
        self.assertEqual(out[0]["rerank_score"], 0.9)
        self.assertEqual(out[1]["rerank_score"], 0.3)
        self.assertEqual(out[2]["rerank_score"], 0.1)
        self.assertEqual(dummy.calls, 1)

    def test_keeps_rrf_fields_and_adds_rerank_score(self):
        dummy = DummyEncoder([0.4, 0.8])
        reranker = Reranker(encoder=dummy)
        hits = [
            _fused_hit("a", "text-a", rrf_score=0.032, dense_rank=2, sparse_rank=1),
            _fused_hit("b", "text-b", rrf_score=0.031, dense_rank=1, sparse_rank=4),
        ]
        out = reranker.rerank("query", hits, top_k=2)
        self.assertEqual(len(out), 2)
        for row in out:
            self.assertIn("rrf_score", row)
            self.assertIn("ranks", row)
            self.assertIn("rerank_score", row)
            self.assertIn("chunk_id", row)
            self.assertIn("citation_id", row)
        # highest score first
        self.assertEqual(out[0]["chunk_id"], "b")
        self.assertEqual(out[0]["rrf_score"], 0.031)
        self.assertEqual(out[0]["ranks"]["dense"], 1)

    def test_top_k_slice(self):
        dummy = DummyEncoder([0.1, 0.9, 0.5])
        reranker = Reranker(encoder=dummy)
        hits = [
            _fused_hit("a", "a"),
            _fused_hit("b", "b"),
            _fused_hit("c", "c"),
        ]
        out = reranker.rerank("query", hits, top_k=2)
        self.assertEqual(len(out), 2)
        self.assertEqual([h["chunk_id"] for h in out], ["b", "c"])
        # ranks must be re-numbered 1..N after sort
        self.assertEqual(out[0]["rank"], 1)
        self.assertEqual(out[1]["rank"], 2)

    def test_does_not_mutate_input(self):
        dummy = DummyEncoder([0.7, 0.2])
        reranker = Reranker(encoder=dummy)
        hits = [
            _fused_hit("a", "text-a"),
            _fused_hit("b", "text-b"),
        ]
        original = [dict(h) for h in hits]  # deep-ish copy of top level
        _ = reranker.rerank("query", hits, top_k=2)
        for before, after in zip(original, hits):
            self.assertNotIn("rerank_score", after)
            self.assertEqual(before["chunk_id"], after["chunk_id"])
            self.assertEqual(before["rrf_score"], after["rrf_score"])

    def test_caps_at_fusion_cut_40(self):
        # 41 hits → encoder must see only 40 pairs
        dummy = DummyEncoder([])  # dynamic length → returns zeros
        reranker = Reranker(encoder=dummy)
        hits = [_fused_hit(f"c{i:02d}", f"text-{i}") for i in range(41)]
        out = reranker.rerank("query", hits, top_k=5)
        self.assertEqual(dummy.calls, 1)
        self.assertEqual(len(dummy.last_pairs), 40)
        self.assertEqual(len(out), 5)

    def test_prefers_embedding_text_when_present(self):
        dummy = DummyEncoder([0.9])
        reranker = Reranker(encoder=dummy)
        hits = [
            {
                "rank": 1,
                "chunk_id": "x",
                "rrf_score": 0.02,
                "ranks": {"dense": 1, "sparse": 1},
                "citation_id": "BNSS-x",
                "text": "should-not-be-used",
                "embedding_text": "preferred-passage",
            }
        ]
        _ = reranker.rerank("query", hits, top_k=1)
        self.assertEqual(dummy.last_pairs[0][1], "preferred-passage")

    def test_invalid_top_k_raises(self):
        dummy = DummyEncoder([0.5])
        reranker = Reranker(encoder=dummy)
        with self.assertRaises(ValueError):
            reranker.rerank("query", [_fused_hit("a", "a")], top_k=0)
        with self.assertRaises(ValueError):
            reranker.rerank("query", [_fused_hit("a", "a")], top_k=-1)

class SearchCompositionTests(unittest.TestCase):
    """Reranker.search must compose hybrid -> rerank and return CLI-shaped JSON.
    Regression pin for the WP5a<->WP5b seam."""

    class _FakeHybrid:
        def __init__(self, results):
            self._results = results
            self.closed = False

        def search(self, query, top_k=3, act=None, fusion_cut=40):
            return {
                "query": query, "act": act,
                "counts": {"dense": 1, "sparse": 1, "fused": 1},
                "candidate_depth": 20, "chunks_sha256": "fake",
                "results": list(self._results),
            }

        def close(self):
            self.closed = True

    class _FakeEncoder:
        def predict(self, pairs):
            return [1.0] * len(pairs)

    def test_search_composes_hybrid_then_rerank(self):
        from backend.app.retrieval.reranker import Reranker

        hit = {"chunk_id": "c1", "text": "Murder is punishable with death.",
               "embedding_text": "BNS | Murder. Murder is punishable with death."}
        reranker = Reranker(encoder=self._FakeEncoder())
        reranker.hybrid = self._FakeHybrid([hit])
        out = reranker.search("murder punishment", top_k=1, act="BNS")
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["counts"]["reranked"], 1)
        self.assertTrue(all("rerank_score" in r for r in out["results"]))
        self.assertEqual(out["results"][0]["rank"], 1)

    def test_close_closes_owned_hybrid_once(self):
        from backend.app.retrieval.reranker import Reranker

        reranker = Reranker()
        fake = self._FakeHybrid([])
        reranker.hybrid = fake
        reranker.close()
        self.assertTrue(fake.closed)
        self.assertIsNone(reranker.hybrid)
        reranker.close()  # idempotent — must not raise

if __name__ == "__main__":
    unittest.main()