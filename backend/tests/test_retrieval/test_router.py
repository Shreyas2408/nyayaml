"""Router tests — fake retriever, no Qdrant."""

import unittest

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.retrieval import router as retrieval_router


class FakeRetriever:
    def search(self, query, top_k, act=None):
        if query == "fail-qdrant":
            from qdrant_client.http.exceptions import ResponseHandlingException

            raise ResponseHandlingException(source="test")
        return {
            "status": "ok",
            "query": query,
            "act": act,
            "top_k": top_k,
            "candidate_depth": 20,
            "counts": {"dense": 20, "sparse": 20, "fused": 1},
            "results": [{"rank": 1, "chunk_id": "chunk-x", "act": act or "BNS"}],
        }


class RouterTests(unittest.TestCase):
    def setUp(self):
        app.dependency_overrides[retrieval_router.get_retriever] = lambda: FakeRetriever()
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()

    def test_health(self):
        r = self.client.get("/api/v1/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"status": "ok"})

    def test_search_ok(self):
        r = self.client.post(
            "/api/v1/retrieval/search",
            json={"query": "theft", "act": "BNSS", "top_k": 3},
        )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["candidate_depth"], 20)
        self.assertEqual(len(body["results"]), 1)

    def test_top_k_zero_422(self):
        r = self.client.post(
            "/api/v1/retrieval/search",
            json={"query": "theft", "top_k": 0},
        )
        self.assertEqual(r.status_code, 422)

    def test_bad_act_422(self):
        r = self.client.post(
            "/api/v1/retrieval/search",
            json={"query": "theft", "act": "IPC"},
        )
        self.assertEqual(r.status_code, 422)

    def test_qdrant_503(self):
        r = self.client.post(
            "/api/v1/retrieval/search",
            json={"query": "fail-qdrant"},
        )
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json()["detail"], "Qdrant unavailable")


if __name__ == "__main__":
    unittest.main()