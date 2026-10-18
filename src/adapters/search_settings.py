"""Explicit, redacted provider configuration; no import-time .env loading."""

import os
from pathlib import Path

from pydantic import ConfigDict, SecretStr

from src.schemas import Contract, NonEmpty


class SearchConfigurationError(ValueError):
    """Controlled message containing environment variable names, never values."""


class SearchSettings(Contract):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)

    pinecone_api_key: SecretStr
    fireworks_api_key: SecretStr
    index_name: NonEmpty
    embedding_model: NonEmpty = "nomic-ai/nomic-embed-text-v1.5"
    dense_field: NonEmpty | None = None
    text_field: NonEmpty | None = None
    ca_file: Path | None = None


def load_search_settings(env_file: Path | None = None) -> SearchSettings:
    from dotenv import dotenv_values

    local = dotenv_values(env_file, interpolate=False) if env_file is not None and env_file.is_file() else {}
    values = {key: os.environ.get(key, local.get(key)) for key in (
        "PINECONE_API_KEY", "PINECONE_INDEX_NAME", "FIREWORKS_API_KEY", "FIREWORKS_EMBEDDING_MODEL",
        "PINECONE_DENSE_FIELD", "PINECONE_TEXT_FIELD", "SSL_CERT_FILE",
    )}
    required = ("PINECONE_API_KEY", "PINECONE_INDEX_NAME", "FIREWORKS_API_KEY")
    missing = [key for key in required if not values[key] or not values[key].strip()]
    if missing:
        raise SearchConfigurationError("Missing configuration: " + ", ".join(missing))
    return SearchSettings(
        pinecone_api_key=SecretStr(values["PINECONE_API_KEY"]),
        fireworks_api_key=SecretStr(values["FIREWORKS_API_KEY"]), index_name=values["PINECONE_INDEX_NAME"],
        embedding_model=values["FIREWORKS_EMBEDDING_MODEL"] or "nomic-ai/nomic-embed-text-v1.5",
        dense_field=values["PINECONE_DENSE_FIELD"] or None, text_field=values["PINECONE_TEXT_FIELD"] or None,
        ca_file=Path(values["SSL_CERT_FILE"]) if values["SSL_CERT_FILE"] else None,
    )
