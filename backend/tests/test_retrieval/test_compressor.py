"""WP5b compressor tests — pure core only. No MiniLM download, no Qdrant, no hybrid."""

import unittest

from backend.app.retrieval.compressor import (
    SENTENCE_THRESHOLD,
    Compressor,
    split_sentences,
)

PASSAGE = "Alpha sentence. Beta sentence. Gamma sentence."
ALPHA, BETA, GAMMA = (0, 15), (16, 30), (31, 46)

BASE_HIT = {
    "citation_id": "BNSS-359",
    "chunk_id": "bnss-359-0",
    "rrf_score": 0.032,
    "ranks": {"dense": 1, "sparse": 2},
    "rerank_score": 5.1,
    "text": PASSAGE,
}


class DummyEncoder:
    """Score source for tests. If exactly one score is configured it is
    broadcast to all pairs (used by the check() probe)."""

    def __init__(self, scores):
        self.scores = list(scores)
        self.calls = 0

    def predict(self, pairs):
        self.calls += 1
        if len(self.scores) == 1:
            return [self.scores[0]] * len(pairs)
        if len(self.scores) != len(pairs):
            raise AssertionError(
                f"DummyEncoder configured with {len(self.scores)} scores; "
                f"predict got {len(pairs)} pairs")
        return list(self.scores)


class ShortScoreEncoder:
    """Deliberately returns one score fewer than pairs (finding 4 fixture)."""

    def predict(self, pairs):
        return [0.5] * (len(pairs) - 1)


def make(hit=None, scores=None):
    dummy = DummyEncoder(scores or [])
    comp = Compressor(reranker=None, encoder=dummy)
    return comp, dummy, dict(BASE_HIT if hit is None else hit)


class SplitSentencesTests(unittest.TestCase):
    def test_measured_offsets_and_roundtrip(self):
        spans = split_sentences(PASSAGE)
        self.assertEqual([s["char_start"] for s in spans], [0, 16, 31])
        self.assertEqual([s["char_end"] for s in spans], [15, 30, 46])
        for s in spans:
            self.assertEqual(PASSAGE[s["char_start"]:s["char_end"]], s["text"])

    def test_legal_references_stay_glued(self):
        sub = ("The court shall proceed under sub-sec. (1) of section 132. "
               "The order so made is final.")
        spans = split_sentences(sub)
        self.assertEqual(len(spans), 2)
        self.assertTrue(spans[0]["text"].endswith("section 132."))
        self.assertIn("(1)", spans[0]["text"])  # NOT its own fragment
        self.assertEqual(spans[1]["text"], "The order so made is final.")

    def test_em_dash_explanations_do_not_fragment(self):
        ext = ("Explanation.—Nothing here is an offence. "
               "Provided that the court agrees.")
        spans = split_sentences(ext)
        self.assertEqual(len(spans), 2)
        self.assertTrue(spans[0]["text"].startswith("Explanation.—"))
        self.assertEqual(spans[1]["text"], "Provided that the court agrees.")

    def test_blank_passage_yields_nothing(self):
        self.assertEqual(split_sentences("   \n  "), [])


class CompressTests(unittest.TestCase):
    def test_empty_query_returns_empty_and_never_predicts(self):
        comp, dummy, hit = make(scores=[0.9, 0.9, 0.9])
        self.assertEqual(comp.compress("", [hit]), [])
        self.assertEqual(comp.compress("   ", [hit]), [])
        self.assertEqual(dummy.calls, 0)

    def test_empty_hits_returns_empty(self):
        comp, dummy, _ = make(scores=[0.9])
        self.assertEqual(comp.compress("bail", []), [])
        self.assertEqual(dummy.calls, 0)

    def test_blank_passage_is_skipped_not_fabricated(self):
        comp, dummy, hit = make(scores=[0.9])
        hit["text"] = "   "
        out = comp.compress_hit("bail", hit)
        self.assertEqual(out["spans"], [])
        self.assertEqual(out["compressed_text"], "")
        self.assertEqual(out["compression_mode"], "skipped")
        self.assertEqual(dummy.calls, 0)

    def test_drop_middle_keeps_survivors_in_document_order(self):
        comp, _, hit = make(scores=[0.9, -1.0, 0.4])
        out = comp.compress_hit("murder punishment", hit)
        self.assertEqual(out["compression_mode"], "threshold")
        self.assertFalse(out["low_confidence"])
        starts = [s["char_start"] for s in out["spans"]]
        self.assertEqual(starts, [0, 31])  # document order, not score order
        self.assertEqual(out["compressed_text"],
                         "Alpha sentence. Gamma sentence.")

    def test_threshold_is_inclusive(self):
        comp, _, hit = make(scores=[0.0, -0.1, 0.0])  # boundary: >= threshold
        out = comp.compress_hit("q", hit)
        self.assertEqual([s["char_start"] for s in out["spans"]], [0, 31])

    def test_all_below_threshold_keeps_single_best_flagged(self):
        comp, _, hit = make(scores=[-2.0, -1.0, -3.0])  # best = Beta
        out = comp.compress_hit("q", hit)
        self.assertEqual(out["compression_mode"], "fallback_best")
        self.assertTrue(out["low_confidence"])
        self.assertEqual(len(out["spans"]), 1)
        span = out["spans"][0]
        self.assertEqual(span["text"], "Beta sentence.")
        self.assertEqual(span["score"], -1.0)

    def test_input_hit_is_not_mutated(self):
        comp, _, hit = make(scores=[0.9, 0.9, 0.9])
        before = dict(hit)
        comp.compress_hit("q", hit)
        self.assertEqual(hit, before)
        self.assertNotIn("spans", hit)

    def test_retrieval_fields_preserved(self):
        comp, _, hit = make(scores=[0.9, 0.9, 0.9])
        out = comp.compress_hit("q", hit)
        for key in ("citation_id", "chunk_id", "rrf_score", "ranks",
                    "rerank_score"):
            self.assertEqual(out[key], hit[key])

    def test_no_paraphrase_substring_invariant(self):
        comp, _, hit = make(scores=[0.5, 0.5, 0.5])
        out = comp.compress_hit("q", hit)
        for s in out["spans"]:
            self.assertIn(s["text"], PASSAGE)
        self.assertTrue(set(out["compressed_text"].split())
                        <= set(PASSAGE.split()))

    def test_score_count_mismatch_fails_closed(self):
        comp = Compressor(reranker=None, encoder=ShortScoreEncoder())
        with self.assertRaises(RuntimeError):
            comp.compress_hit("q", dict(BASE_HIT))

    def test_passage_field_recorded_and_offsets_index_it(self):
        hit = dict(BASE_HIT)
        hit["embedding_text"] = "CTX " + PASSAGE
        comp = Compressor(reranker=None, encoder=DummyEncoder([0.9, 0.9, 0.9]))
        out = comp.compress_hit("q", hit)
        self.assertEqual(out["passage_field"], "embedding_text")
        for s in out["spans"]:
            self.assertEqual(hit["embedding_text"][s["char_start"]:s["char_end"]],
                             s["text"])
        text_only = {"text": PASSAGE}
        out2 = comp.compress_hit("q", text_only)
        self.assertEqual(out2["passage_field"], "text")

    def test_check_is_honest_declared_vs_passed(self):
        cold = Compressor()  # nothing loaded, nothing injected
        info = cold.check()
        self.assertEqual(info["status"], "declared")
        self.assertFalse(info["verified"])

        warm = Compressor(encoder=DummyEncoder([0.5]))
        info = warm.check()
        self.assertEqual(info["status"], "passed")
        self.assertTrue(info["verified"])
        self.assertEqual(info["probe_score"], 0.5)

    def test_threshold_constant_frozen(self):
        self.assertEqual(SENTENCE_THRESHOLD, 0.0)  # WP5b freeze, no CLI flag


if __name__ == "__main__":
    unittest.main()