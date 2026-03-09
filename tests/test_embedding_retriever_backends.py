from __future__ import annotations

import sys
from pathlib import Path
import unittest

import numpy as np

# Add tests directory to sys.path for the eval package
sys.path.insert(0, str(Path(__file__).parent))

from eval.retrieval.embedding_retriever import MiniLMRetriever


class _FakeModel:
    def __init__(self, vec: list[float]) -> None:
        self._vec = np.asarray(vec, dtype=np.float32)

    def encode(self, texts, convert_to_numpy=True, normalize_embeddings=True):
        return np.asarray([self._vec for _ in texts], dtype=np.float32)


class TestEmbeddingRetrieverBackends(unittest.TestCase):
    def test_mpnet_backend_uses_embedding_path(self) -> None:
        retriever = MiniLMRetriever.__new__(MiniLMRetriever)
        retriever._backend = "mpnet"
        retriever._model = _FakeModel([1.0, 0.0])
        retriever._embeddings = np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
        retriever._vectorizer = None
        retriever._tfidf_matrix = None
        retriever._docs = [
            {"entity_id": "E1", "source": "doc one"},
            {"entity_id": "E2", "source": "doc two"},
        ]

        hits = retriever.search("query", top_k=1)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["entity_id"], "E1")


if __name__ == "__main__":
    unittest.main()
