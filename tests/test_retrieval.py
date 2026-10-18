"""Offline unit tests only; fixture scores/embeddings are NOT live API results."""

import json
import math
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from pinecone import Document
from pinecone.models import IndexSchema
from pinecone.models.indexes.schema import DenseVectorField, FullTextSearchConfig, StringField

from scripts.generate_data import generate
from src.adapters.base import AdapterUnavailable
from src.adapters.fireworks_embeddings import ENDPOINT, FireworksEmbeddingAdapter
from src.adapters.pinecone_documents import IndexCompatibilityError, PineconeDocumentAdapter, inspect_schema
from src.retrieval.corpus import build_corpus, corpus_digest, exact_tokens
from src.retrieval.dense import DenseRetriever
from src.retrieval.hybrid import HybridRetriever, fuse_hits
from src.retrieval.metrics import recall_at_5, relevant_source_ids
from src.retrieval.models import CorpusDocument, EmbeddingBatch, IndexConfiguration, SearchHit


@pytest.fixture
def corpus() -> tuple[CorpusDocument, ...]:
    return build_corpus(generate()[0])


def configuration(full_text: bool = True) -> IndexConfiguration:
    return IndexConfiguration(index_name="synthetic-unit-index", dimension=3, metric="cosine", dense_field="embedding", text_field="body" if full_text else None, ready=True, schema_fields=("embedding", "body"))


def fixture_hit(document: CorpusDocument, score: float = 1.0, kind: str = "dense_similarity") -> SearchHit:
    return SearchHit(document=document, score=score, score_kind=kind, provider="unit_test_fixture")


def returned_document(adapter: PineconeDocumentAdapter, document: CorpusDocument, score: float = 1.0) -> Document:
    return Document({"_id": document.source_id, "_score": score, "body": document.text,
                     "source_payload": document.model_dump_json(), "corpus_id": adapter._digest,
                     "embedding_model": adapter.embedding_model, "embedding_dimension": 3, "synthetic": True})


class FixtureEmbeddings:
    """Transport-independent constant vectors for unit wiring tests only."""
    model = "unit_fixture_embeddings"
    dimension = 3

    def embed(self, texts: tuple[str, ...], purpose: str) -> EmbeddingBatch:
        return EmbeddingBatch(model=self.model, dimension=3, vectors=tuple((1.0, 0.0, 0.0) for _ in texts))


def test_corpus_has_only_80_sources_and_real_synthetic_provenance(corpus: tuple[CorpusDocument, ...]) -> None:
    assert len(corpus) == len({d.source_id for d in corpus}) == 80
    manuals = [d for d in corpus if d.source_kind == "manual"]
    assert len(manuals) == 30
    assert all(d.source_doc and d.page and d.source_version for d in manuals)
    assert all(d.source_id.startswith(("SYN-SEC-", "SYN-WO-")) for d in corpus)
    assert corpus_digest(corpus) == corpus_digest(tuple(reversed(corpus)))
    assert all(code in d.text for d in corpus for code in d.alarm_codes)
    assert all(part in d.text for d in corpus for part in d.part_numbers)


def test_exact_tokens_do_not_match_partial_codes() -> None:
    assert exact_tokens("syn-alm-001 and SYN-PART-006") == ("SYN-ALM-001", "SYN-PART-006")
    assert exact_tokens("SYN-ALM-0010 SYN-PART-006x") == ()


def test_recall_is_fraction_of_independent_relevant_documents(corpus: tuple[CorpusDocument, ...]) -> None:
    relevant = relevant_source_ids(corpus, "SYN-PART-006")
    assert len(relevant) == 4
    hits = tuple(fixture_hit(d) for d in corpus if d.source_id in relevant)
    assert recall_at_5(hits, relevant) == 1.0
    assert recall_at_5(hits[:2], relevant) == 0.5
    assert recall_at_5(hits * 2, relevant) == 1.0
    with pytest.raises(ValueError):
        recall_at_5((), frozenset())


@pytest.mark.parametrize("model", ["fireworks/qwen3-embedding-8b", "nomic-ai/nomic-embed-text-v1.5"])
def test_fireworks_documented_payload_order_and_normalization(model: str) -> None:
    seen = []
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        seen.append(payload)
        assert str(request.url) == ENDPOINT and request.method == "POST"
        assert payload["dimensions"] == 3
        return httpx.Response(200, json={"model": model, "data": [
            {"index": 1, "embedding": [0.0, 0.0, 2.0]}, {"index": 0, "embedding": [3.0, 4.0, 0.0]},
        ]})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = FireworksEmbeddingAdapter("unit-fixture-key", model, 3, client=client)
    batch = adapter.embed(("first", "second"), "document")
    assert batch.vectors == ((0.6, 0.8, 0.0), (0.0, 0.0, 1.0))
    assert seen[0]["input"][0] == ("search_document: first" if model.startswith("nomic") else "first")
    adapter.close()


@pytest.mark.parametrize("problem", ["dimension", "zero", "indices", "model", "http", "nonfinite"])
def test_invalid_embedding_response_fails_closed(problem: str) -> None:
    model = "fireworks/qwen3-embedding-8b"
    response = {"model": model, "data": [{"index": 0, "embedding": [1.0, 0.0, 0.0]}]}
    if problem == "dimension": response["data"][0]["embedding"] = [1.0]
    if problem == "zero": response["data"][0]["embedding"] = [0.0, 0.0, 0.0]
    if problem == "indices": response["data"][0]["index"] = 2
    if problem == "model": response["model"] = "different-model"
    if problem == "nonfinite": response["data"][0]["embedding"] = [float("nan"), 0.0, 0.0]
    def handler(request: httpx.Request) -> httpx.Response:
        if problem == "http":
            return httpx.Response(401, text="unit-secret-must-not-leak")
        return httpx.Response(200, content=json.dumps(response))
    adapter = FireworksEmbeddingAdapter("unit-fixture-key", model, 3, client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(AdapterUnavailable) as exc:
        adapter.embed(("synthetic query",), "query")
    assert "unit-secret" not in str(exc.value)
    adapter.close()


def test_fixed_embedding_model_dimension_is_not_guessed() -> None:
    with pytest.raises(ValueError, match="incompatible"):
        FireworksEmbeddingAdapter("unit-key", "BAAI/bge-small-en-v1.5", 768)
    with pytest.raises(ValueError, match="not verified"):
        FireworksEmbeddingAdapter("unit-key", "arbitrary-chat-model", 3)


def test_verified_canonical_qwen_response_alias_is_accepted() -> None:
    canonical = "accounts/fireworks/models/qwen3-embedding-8b"
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["input"][0].startswith("Instruct: ")
        assert "\nQuery:Synthetic unit query" in json.loads(request.content)["input"][0]
        return httpx.Response(200, json={"model": canonical, "data": [{"index": 0, "embedding": [1.0, 0.0, 0.0]}]})
    adapter = FireworksEmbeddingAdapter("unit-fixture-key", "fireworks/qwen3-embedding-8b", 3, client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert adapter.embed(("Synthetic unit query",), "query").dimension == 3
    assert adapter.observed_model == canonical
    adapter.close()


def test_schema_inspection_detects_full_text_and_rejects_missing_fields() -> None:
    fields = {"embedding": DenseVectorField(dimension=3, metric="cosine"), "body": StringField(full_text_search=FullTextSearchConfig(language="en"))}
    description = SimpleNamespace(name="unit-index", schema=IndexSchema(fields=fields), status=SimpleNamespace(ready=True))
    assert inspect_schema(description).text_field == "body"
    assert inspect_schema(description).dimension == 3
    with pytest.raises(IndexCompatibilityError): inspect_schema(description, dense_override="missing")
    with pytest.raises(IndexCompatibilityError): inspect_schema(description, text_override="missing")
    description.status.ready = False
    with pytest.raises(IndexCompatibilityError, match="not ready"): inspect_schema(description)
    description.status.ready = True
    description.schema = IndexSchema(fields={"_values": fields["embedding"]})
    assert inspect_schema(description).text_field is None


def test_indexing_batches_all_sources_with_stable_ids_and_namespace(corpus: tuple[CorpusDocument, ...]) -> None:
    index = MagicMock()
    index.documents.upsert.side_effect = lambda **kwargs: SimpleNamespace(upserted_count=len(kwargs["documents"]))
    adapter = PineconeDocumentAdapter(MagicMock(), index, configuration(), corpus, FixtureEmbeddings.model)
    assert adapter.upsert(FixtureEmbeddings()) == 80
    assert index.documents.upsert.call_count == 5
    rows = [d for call in index.documents.upsert.call_args_list for d in call.kwargs["documents"]]
    assert {row["_id"] for row in rows} == {d.source_id for d in corpus}
    assert all(row["synthetic"] and row["source_payload"] and row["body"] for row in rows)
    same = PineconeDocumentAdapter(MagicMock(), MagicMock(), configuration(), corpus, FixtureEmbeddings.model)
    different = PineconeDocumentAdapter(MagicMock(), MagicMock(), configuration(), corpus, "different-unit-model")
    assert adapter.namespace == same.namespace != different.namespace


@pytest.mark.parametrize("query", ["SYN-ALM-001", "SYN-ALM-020", "SYN-PART-006", "SYN-PART-007"])
def test_exact_token_search_and_citation_ids_unit_fixture(corpus: tuple[CorpusDocument, ...], query: str) -> None:
    index = MagicMock()
    adapter = PineconeDocumentAdapter(MagicMock(), index, configuration(), corpus, FixtureEmbeddings.model)
    relevant = relevant_source_ids(corpus, query)
    index.documents.search.return_value = SimpleNamespace(matches=[returned_document(adapter, d, 2.5) for d in corpus if d.source_id in relevant])
    hits = adapter.lexical_search(query, 5)
    assert recall_at_5(hits, relevant) == 1.0  # Unit-fixture wiring, NOT live recall.
    assert all(h.score_kind == "bm25" and h.score == 2.5 for h in hits)
    arguments = index.documents.search.call_args.kwargs
    assert len(arguments["score_by"]) == 1
    field = "alarm_codes" if "ALM" in query else "part_numbers"
    assert {field: {"$in": [query]}} in arguments["filter"]["$and"]


@pytest.mark.parametrize("problem", ["unknown_id", "text", "corpus", "score", "synthetic"])
def test_invalid_provider_citations_and_scores_rejected(corpus: tuple[CorpusDocument, ...], problem: str) -> None:
    index = MagicMock()
    adapter = PineconeDocumentAdapter(MagicMock(), index, configuration(), corpus, FixtureEmbeddings.model)
    raw = returned_document(adapter, corpus[0]).to_dict()
    key, value = {"unknown_id": ("_id", "SYN-SEC-999"), "text": ("body", "fabricated text"),
                  "corpus": ("corpus_id", "stale"), "score": ("_score", float("nan")), "synthetic": ("synthetic", False)}[problem]
    raw[key] = value
    index.documents.search.return_value = SimpleNamespace(matches=[Document(raw)])
    with pytest.raises(AdapterUnavailable): adapter.dense_search((1.0, 0.0, 0.0), 5)


def test_fusion_keeps_scores_separate_and_deduplicates(corpus: tuple[CorpusDocument, ...]) -> None:
    dense = (fixture_hit(corpus[0], 0.9), fixture_hit(corpus[1], 0.8))
    lexical = (fixture_hit(corpus[1], 4.2, "bm25"),)
    merged = fuse_hits(dense, lexical)
    assert len(merged) == 2
    assert merged[0].document == corpus[1]
    assert merged[0].score == pytest.approx(1 / 62 + 2 / 61)
    assert merged[0].dense_score == 0.8 and merged[0].lexical_score == 4.2
    assert merged[0].score_kind == "weighted_rrf"
    with pytest.raises(ValueError, match="Duplicate"): fuse_hits(dense * 2, lexical)


def test_independent_dense_and_hybrid_services_and_unavailable_hybrid(corpus: tuple[CorpusDocument, ...]) -> None:
    index = MagicMock()
    index.configuration, index.embedding_model = configuration(), FixtureEmbeddings.model
    index.dense_search.return_value = (fixture_hit(corpus[0]),)
    index.lexical_search.return_value = (fixture_hit(corpus[1], 5.0, "bm25"),)
    dense = DenseRetriever(FixtureEmbeddings(), index)
    assert len(dense.search("SYN-ALM-001")) == 1
    index.lexical_search.assert_not_called()
    assert len(HybridRetriever(FixtureEmbeddings(), index).search("SYN-ALM-001")) == 2
    with pytest.raises(ValueError): HybridRetriever(FixtureEmbeddings(), index).search("query", top_k=0)
    index.configuration = configuration(full_text=False)
    with pytest.raises(AdapterUnavailable): HybridRetriever(FixtureEmbeddings(), index).search("query")
