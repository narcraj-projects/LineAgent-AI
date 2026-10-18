"""Redacted config and live-index creation safety, using isolated unit fixtures."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pinecone import Document
from pinecone.models import IndexSchema
from pinecone.models.indexes.schema import DenseVectorField, FullTextSearchConfig, StringField
from pydantic import SecretStr

from scripts.generate_data import generate
from scripts.index_pinecone import run
from src.adapters.base import AdapterUnavailable
from src.adapters.pinecone_documents import IndexCompatibilityError, PineconeDocumentAdapter
from src.adapters.search_settings import SearchConfigurationError, SearchSettings, load_search_settings
from src.retrieval.corpus import build_corpus
from src.retrieval.metrics import relevant_source_ids
from src.retrieval.models import IndexConfiguration


@pytest.fixture(autouse=True)
def absent_provider_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("PINECONE_API_KEY", "PINECONE_INDEX_NAME", "FIREWORKS_API_KEY", "FIREWORKS_EMBEDDING_MODEL", "PINECONE_DENSE_FIELD", "PINECONE_TEXT_FIELD", "SSL_CERT_FILE"):
        monkeypatch.delenv(key, raising=False)


def test_missing_keys_are_not_run_and_never_have_fake_results(tmp_path: Path) -> None:
    with pytest.raises(SearchConfigurationError): load_search_settings()
    report = run(tmp_path / "missing.env", tmp_path)
    assert report["status"] == "NOT_RUN" and report["results"] == []
    assert "FIREWORKS_API_KEY" in report["blocker"]


def test_embedding_model_and_chat_model_are_separate(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text('PINECONE_API_KEY=unit-fixture-pinecone\nPINECONE_INDEX_NAME=unit-index\nFIREWORKS_API_KEY=unit-fixture-fireworks\nFIREWORKS_MODEL=unit-chat-model\n', encoding="utf-8")
    settings = load_search_settings(env)
    assert settings.embedding_model == "fireworks/qwen3-embedding-8b"
    assert "unit-fixture-" not in repr(settings)


def test_upsert_visibility_checks_search_publication_not_just_fetch() -> None:
    corpus = build_corpus(generate()[0])
    config = IndexConfiguration(
        index_name="unit-index", dimension=3, metric="cosine", dense_field="embedding", text_field="body", ready=True, schema_fields=("embedding", "body"),
    )
    index = MagicMock()
    adapter = PineconeDocumentAdapter(MagicMock(), index, config, corpus, "unit-model")
    records = {d.source_id: Document({"_id": d.source_id, "_score": 0.5, "body": d.text,
               "source_payload": d.model_dump_json(), "corpus_id": adapter._digest,
               "embedding_model": "unit-model", "embedding_dimension": 3, "synthetic": True}) for d in corpus}
    first = min(records)
    index.documents.fetch.side_effect = [SimpleNamespace(documents=records), SimpleNamespace(documents={first: Document({"_id": first, "embedding": [1.0, 0.0, 0.0]})})]
    index.documents.search.return_value = SimpleNamespace(matches=[])
    with pytest.raises(AdapterUnavailable, match="not fully visible"):
        adapter.verify_visible()
    index.documents.fetch.side_effect = [SimpleNamespace(documents=records), SimpleNamespace(documents={first: Document({"_id": first, "embedding": [1.0, 0.0, 0.0]})})]
    relevant = relevant_source_ids(corpus, corpus[0].alarm_codes[0])
    index.documents.search.side_effect = [SimpleNamespace(matches=list(records.values())), SimpleNamespace(matches=[records[key] for key in records if key in relevant])]
    adapter.verify_visible()


def test_creation_requires_explicit_permission_and_embedding_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    from pinecone import NotFoundError
    import pinecone

    settings = SearchSettings(pinecone_api_key=SecretStr("unit-pinecone"), fireworks_api_key=SecretStr("unit-fireworks"), index_name="unit-index")
    client = MagicMock()
    client.indexes.describe.side_effect = NotFoundError("Unit-test missing index")
    monkeypatch.setattr(pinecone, "Pinecone", lambda **kwargs: client)
    corpus = build_corpus(generate()[0])
    with pytest.raises(IndexCompatibilityError, match="explicit"):
        PineconeDocumentAdapter.connect(settings, corpus)
    client.indexes.create.assert_not_called()
    with pytest.raises(IndexCompatibilityError, match="probe"):
        PineconeDocumentAdapter.connect(settings, corpus, create_dimension=3)
    client.indexes.create.assert_not_called()
    events = []
    def failed_probe(dimension: int) -> None:
        raise AdapterUnavailable("Unit-test failed embedding probe")
    with pytest.raises(AdapterUnavailable): PineconeDocumentAdapter.connect(settings, corpus, 3, failed_probe)
    client.indexes.create.assert_not_called()
    schema = IndexSchema(fields={"embedding": DenseVectorField(dimension=3, metric="cosine"), "body": StringField(full_text_search=FullTextSearchConfig(language="en"))})
    def create(**kwargs: object) -> SimpleNamespace:
        assert events == ["probe"]
        return SimpleNamespace(name="unit-index", host="https://unit.invalid", schema=schema, status=SimpleNamespace(ready=True))
    client.indexes.create.side_effect = create
    PineconeDocumentAdapter.connect(settings, corpus, 3, lambda dimension: events.append("probe"))
    assert client.indexes.create.call_count == 1
