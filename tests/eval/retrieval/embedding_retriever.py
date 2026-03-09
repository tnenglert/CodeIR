"""Local sentence-transformer embedding retriever for source-only corpus documents."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import numpy as np

class MiniLMRetriever:
    """Cosine-similarity retriever backed by local sentence-transformer embeddings."""

    def __init__(self, corpus_docs: List[Dict[str, Any]], model_name: str = "all-MiniLM-L6-v2") -> None:
        self._docs = corpus_docs
        texts = [str(d.get("search_text", "")) for d in self._docs]

        self._model = None
        self._vectorizer = None
        self._tfidf_matrix = None

        SentenceTransformer = None
        try:  # pragma: no cover - exercised at runtime based on local env
            from sentence_transformers import SentenceTransformer as _SentenceTransformer
            SentenceTransformer = _SentenceTransformer
        except Exception:
            SentenceTransformer = None

        if SentenceTransformer is not None:
            self._model = SentenceTransformer(model_name)
            emb = self._model.encode(texts, convert_to_numpy=True, normalize_embeddings=True)
            self._embeddings = np.asarray(emb, dtype=np.float32)
            # Derive backend name from model
            if "mpnet" in model_name.lower():
                self._backend = "mpnet"
            elif "minilm" in model_name.lower():
                self._backend = "minilm"
            else:
                self._backend = "sentence_transformers"
        else:
            # Fallback for offline environments: deterministic TF-IDF cosine retrieval.
            from sklearn.feature_extraction.text import TfidfVectorizer
            self._backend = "tfidf_fallback"
            self._vectorizer = TfidfVectorizer(lowercase=True, ngram_range=(1, 2), min_df=1)
            self._tfidf_matrix = self._vectorizer.fit_transform(texts)
            self._embeddings = None

    @classmethod
    def from_corpus_path(
        cls,
        corpus_path: Path,
        model_name: str = "all-MiniLM-L6-v2",
    ) -> "MiniLMRetriever":
        data = json.loads(corpus_path.read_text(encoding="utf-8"))
        docs = list(data.get("documents", []))
        return cls(docs, model_name=model_name)

    def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        query = str(query or "").strip()
        if not query:
            return []

        if self._backend in {"minilm", "mpnet", "sentence_transformers"}:
            if self._model is None or self._embeddings is None:
                return []
            q = self._model.encode([query], convert_to_numpy=True, normalize_embeddings=True)
            qv = np.asarray(q[0], dtype=np.float32)
            sims = self._embeddings @ qv
            if sims.size == 0:
                return []
        else:
            if self._vectorizer is None or self._tfidf_matrix is None:
                return []
            qv = self._vectorizer.transform([query])
            sims_arr = (self._tfidf_matrix @ qv.T).toarray().ravel()
            sims = np.asarray(sims_arr, dtype=np.float32)
            if sims.size == 0:
                return []

        k = min(int(top_k), sims.shape[0])
        top_idx = np.argpartition(-sims, k - 1)[:k]
        top_idx = top_idx[np.argsort(-sims[top_idx])]

        out: List[Dict[str, Any]] = []
        for idx in top_idx:
            doc = self._docs[int(idx)]
            out.append(
                {
                    "entity_id": str(doc["entity_id"]),
                    "qualified_name": str(doc.get("qualified_name", "")),
                    "kind": str(doc.get("kind", "")),
                    "file_path": str(doc.get("file_path", "")),
                    "source": str(doc.get("source", "")),
                    "score": float(sims[int(idx)]),
                }
            )
        return out

    @property
    def backend(self) -> str:
        return self._backend


__all__ = ["MiniLMRetriever"]
