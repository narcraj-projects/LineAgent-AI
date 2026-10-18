"""Explicitly gated live retrieval measurements; never replaced by fixture scores."""

from collections.abc import Iterator
import os
from pathlib import Path

import pytest

from src.adapters.search_settings import SearchConfigurationError, load_search_settings
from src.retrieval.corpus import build_corpus, load_dataset
from src.retrieval.live import LiveRetrieval, open_live_retrieval
from src.retrieval.metrics import EXACT_CASES, recall_at_5, relevant_source_ids

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def live_retrieval() -> Iterator[LiveRetrieval]:
    if os.environ.get("LINEMEDIC_RUN_LIVE_RETRIEVAL_TESTS") != "1":
        pytest.skip("Live retrieval not opted in; set LINEMEDIC_RUN_LIVE_RETRIEVAL_TESTS=1 after configuring providers and creating a compatible index.")
    root = Path(__file__).resolve().parents[1]
    try:
        settings = load_search_settings(root / ".env")
    except SearchConfigurationError:
        pytest.skip("Pinecone/Fireworks credentials missing; no live retrieval run.")
    documents = build_corpus(load_dataset(root / "data"))
    live = open_live_retrieval(settings, documents)
    try:
        live.index_documents()
        yield live
    finally:
        live.close()


@pytest.mark.parametrize("token", EXACT_CASES)
@pytest.mark.parametrize("mode", ["dense", "hybrid"])
def test_live_exact_token_recall_at_5_and_citations(live_retrieval: LiveRetrieval, token: str, mode: str, record_property: object) -> None:
    root = Path(__file__).resolve().parents[1]
    documents = build_corpus(load_dataset(root / "data"))
    expected = relevant_source_ids(documents, token)
    retriever = live_retrieval.dense if mode == "dense" else live_retrieval.hybrid
    hits = retriever.search(token, top_k=5)
    recall = recall_at_5(hits, expected)
    record_property("recall_at_5", recall)
    assert hits and all(hit.provider == "pinecone:live" for hit in hits)
    assert all(hit.document.source_id in {d.source_id for d in documents} for hit in hits)
    assert recall > 0, f"No relevant exact-token documents in live {mode} top 5"
