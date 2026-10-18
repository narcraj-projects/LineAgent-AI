"""Documented Fireworks REST embeddings API; no guessed chat-model substitution."""

import math
from pathlib import Path
import ssl
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from src.adapters.base import AdapterUnavailable
from src.retrieval.models import EmbeddingBatch

ENDPOINT = "https://api.fireworks.ai/inference/v1/embeddings"
QUERY_INSTRUCTION = "Retrieve synthetic equipment manual sections and work orders relevant to the alarm code, spare part number, or troubleshooting question."
PROFILES = {
    "fireworks/qwen3-embedding-8b": (True, None),
    "nomic-ai/nomic-embed-text-v1.5": (True, None),
    "BAAI/bge-small-en-v1.5": (False, 384),
}
# The canonical Qwen ID below was verified in a real Fireworks embedding response.
# This is an exact allowlist, never a suffix/substring model identity match.
RESPONSE_MODELS = {
    "fireworks/qwen3-embedding-8b": frozenset({"fireworks/qwen3-embedding-8b", "accounts/fireworks/models/qwen3-embedding-8b"}),
    "nomic-ai/nomic-embed-text-v1.5": frozenset({"nomic-ai/nomic-embed-text-v1.5"}),
    "BAAI/bge-small-en-v1.5": frozenset({"BAAI/bge-small-en-v1.5"}),
}


class EmbeddingRow(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, hide_input_in_errors=True)
    index: int = Field(ge=0)
    embedding: list[float] = Field(min_length=1)


class EmbeddingResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, hide_input_in_errors=True)
    data: list[EmbeddingRow] = Field(min_length=1)
    model: str


class FireworksEmbeddingAdapter:
    def __init__(self, api_key: str, model: str, dimension: int, ca_file: Path | None = None, client: httpx.Client | None = None) -> None:
        if model not in PROFILES:
            raise ValueError("Embedding model profile not verified against current provider documentation")
        resizable, fixed = PROFILES[model]
        if dimension <= 0 or (not resizable and dimension != fixed):
            raise ValueError("Requested index dimension is incompatible with the documented embedding model")
        self.model, self.dimension = model, dimension
        self.query_preprocessing = "qwen-instruction-v1" if model == "fireworks/qwen3-embedding-8b" else "nomic-search-prefix-v1" if model.startswith("nomic-ai/") else "plain-v1"
        self.observed_model: str | None = None
        self._key = api_key
        self._client = client or httpx.Client(
            verify=ssl.create_default_context(cafile=str(ca_file)) if ca_file else True,
            timeout=30.0, follow_redirects=False,
        )

    def embed(self, texts: tuple[str, ...], purpose: Literal["query", "document"]) -> EmbeddingBatch:
        if not texts or len(texts) > 16 or any(not text.strip() for text in texts):
            raise ValueError("Embedding batches must contain 1-16 nonblank texts")
        if purpose not in {"query", "document"}:
            raise ValueError("Unknown embedding purpose")
        inputs = list(texts)
        if self.model.startswith("nomic-ai/"):
            inputs = [f"search_{purpose}: {text}" for text in texts]
        elif self.model == "fireworks/qwen3-embedding-8b" and purpose == "query":
            # Qwen's official model card specifies this query-only format.
            inputs = [f"Instruct: {QUERY_INSTRUCTION}\nQuery:{text}" for text in texts]
        payload: dict[str, Any] = {"model": self.model, "input": inputs}
        if PROFILES[self.model][0]:
            payload["dimensions"] = self.dimension
        try:
            response = self._client.post(ENDPOINT, headers={"Authorization": f"Bearer {self._key}"}, json=payload)
            response.raise_for_status()
            result = EmbeddingResponse.model_validate_json(response.content)
            if len(result.data) != len(texts) or {r.index for r in result.data} != set(range(len(texts))):
                raise ValueError("Embedding response indices do not match inputs")
            if result.model not in RESPONSE_MODELS[self.model]:
                raise ValueError("Provider returned a different embedding model")
            vectors = []
            for row in sorted(result.data, key=lambda row: row.index):
                if len(row.embedding) != self.dimension or any(not math.isfinite(v) for v in row.embedding):
                    raise ValueError("Provider embedding has invalid values or dimension")
                norm = math.hypot(*row.embedding)
                if not math.isfinite(norm) or norm == 0:
                    raise ValueError("Provider returned a zero or invalid embedding")
                vectors.append(tuple(value / norm for value in row.embedding))
            self.observed_model = result.model
            return EmbeddingBatch(model=self.model, dimension=self.dimension, vectors=tuple(vectors))
        except Exception:
            raise AdapterUnavailable("Fireworks embedding request/validation failed; provider details withheld.") from None

    def close(self) -> None:
        self._client.close()
