"""Real LangChain plumbing with explicit synthetic provider doubles."""
from pathlib import Path
from unittest.mock import MagicMock
import json

from langchain_core.documents import Document
from langchain_core.vectorstores import VectorStore
import pytest

from src.adapters.langchain_documents import LangChainPDFLoader, corpus_documents, split_verified_section
from src.adapters.langchain_retrieval import AuditedVectorStore, ValidatedEmbeddings, ingest_with_langchain
from src.retrieval.corpus import build_corpus, load_dataset
from src.retrieval.models import EmbeddingBatch
from src.retrieval.pdf_metadata import build_pdf_metadata, langchain_pdf_namespace_tag, pdf_namespace_tag
from tests.test_retrieval import FixtureEmbeddings, configuration, fixture_hit


def test_loader_and_langchain_splitter_preserve_all_80_ids_and_pdf_provenance():
    baseline = load_dataset(Path("data"))
    loader = LangChainPDFLoader(Path("output/pdf/manifest.json"), baseline)
    documents = loader.load()
    assert len(documents) == len({d.id for d in documents}) == 80
    assert loader.parsed is not None and loader.parsed.dataset == baseline
    expected = build_corpus(baseline)
    assert documents == corpus_documents(expected, build_pdf_metadata(loader.parsed))
    manual = next(d for d in documents if d.id == "SYN-SEC-007")
    provenance = json.loads(manual.metadata["rag_metadata"])
    assert provenance["pdf_page"] == 7 and provenance["logical_page"] == 1
    assert len(loader.parsed.chunks) == 30
    assert langchain_pdf_namespace_tag(loader.parsed.manifest) != pdf_namespace_tag(loader.parsed.manifest)


def test_splitter_never_silently_joins_or_rechunks_canonical_sections(monkeypatch):
    text = "Canonical synthetic section. " * 6
    assert split_verified_section(text, "SYN-SEC-001") == text
    monkeypatch.setattr("src.adapters.langchain_documents.RecursiveCharacterTextSplitter.create_documents",
        lambda *args, **kwargs: [Document(page_content="changed", metadata={"section_id":"SYN-SEC-001"})])
    with pytest.raises(ValueError, match="changed"):
        split_verified_section(text, "SYN-SEC-001")


def test_ingestion_runs_langchain_embeddings_and_checks_full_acknowledgement():
    corpus = build_corpus(load_dataset(Path("data")))[:2]
    embedder = FixtureEmbeddings()
    embedder.embed = MagicMock(wraps=embedder.embed)
    index = MagicMock()
    index.embedding_model, index.configuration = embedder.model, configuration()
    def upsert(port):
        batch = port.embed(tuple(d.text for d in corpus), "document")
        assert isinstance(port.embeddings, ValidatedEmbeddings)
        assert batch.model == embedder.model and len(batch.vectors) == 2
        return 2
    commit = MagicMock(side_effect=upsert)
    assert ingest_with_langchain(embedder, index, corpus, None, commit) == 2
    commit.assert_called_once()
    assert embedder.embed.call_args.args[1] == "document"
    with pytest.raises(ValueError, match="acknowledgement"):
        ingest_with_langchain(embedder, index, corpus, None, lambda port: 1)


@pytest.mark.parametrize("alteration", ["text", "provenance", "duplicate", "missing", "id"])
def test_ingestion_rejects_changed_documents_before_embedding_or_write(alteration):
    corpus = build_corpus(load_dataset(Path("data")))[:2]
    embedder = FixtureEmbeddings()
    embedder.embed = MagicMock(side_effect=AssertionError("must not embed"))
    index = MagicMock(embedding_model=embedder.model, configuration=configuration())
    commit = MagicMock(side_effect=AssertionError("must not write"))
    documents = corpus_documents(corpus)
    if alteration == "text":
        documents[0].page_content += " altered"
    elif alteration == "provenance":
        documents[0].metadata["extra"] = "unverified"
    elif alteration == "duplicate":
        documents[1] = documents[0]
    elif alteration == "missing":
        documents.pop()
    else:
        documents[0].id = "SYN-SEC-999"
    with pytest.raises(ValueError, match="disagree"):
        ingest_with_langchain(embedder, index, corpus, None, commit, documents)
    embedder.embed.assert_not_called()
    commit.assert_not_called()


def test_real_langchain_retriever_preserves_scores_context_and_metadata():
    corpus = build_corpus(load_dataset(Path("data")))
    source = next(d for d in corpus if "SYN-EQ-001" in d.equipment_ids)
    hit = fixture_hit(source, .81)
    embedder = FixtureEmbeddings()
    index = MagicMock(embedding_model=embedder.model, configuration=configuration())
    index.dense_search.return_value = (hit,)
    store = AuditedVectorStore(embedder, index)
    assert isinstance(store, VectorStore)
    result = store.as_retriever(search_kwargs={"k":5,"equipment_id":"SYN-EQ-001"}).invoke("synthetic alarm")
    assert result[0].id == source.source_id and result[0].page_content == source.text
    checked = json.loads(result[0].metadata["search_hit"])
    assert checked["score"] == .81 and checked["provider"] == "unit_test_fixture"
    assert index.dense_search.call_args.args[2] == "SYN-EQ-001"
    index.dense_search.return_value = (hit, hit)
    with pytest.raises(ValueError, match="conflicting"):
        store.as_retriever().invoke("synthetic alarm")


def test_langchain_embedding_interface_rejects_a_wrong_provider_identity():
    embedder = FixtureEmbeddings()
    embedder.embed = MagicMock(return_value=EmbeddingBatch(model="wrong",dimension=3,vectors=((1.0,0.0,0.0),)))
    with pytest.raises(ValueError, match="configured model"):
        ValidatedEmbeddings(embedder).embed_query("synthetic")
