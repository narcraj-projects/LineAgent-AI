"""Explicit local/environment configuration; never load secrets on module import."""

import os
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import ConfigDict, Field, SecretStr, model_validator

from src.schemas import Contract, NonEmpty


class Neo4jConfigurationError(ValueError):
    """Controlled configuration message that contains variable names only."""


class Neo4jSettings(Contract):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)

    uri: SecretStr
    username: SecretStr
    password: SecretStr
    database: NonEmpty = "neo4j"
    ca_file: Path | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def validate_connection_fields(self) -> "Neo4jSettings":
        fields = {"NEO4J_URI": self.uri, "NEO4J_USERNAME": self.username, "NEO4J_PASSWORD": self.password}
        for name, value in fields.items():
            plain = value.get_secret_value()
            if not plain.strip() or any(token in plain for token in ("YOUR-INSTANCE", "YOUR-ACTUAL-PASSWORD", "<your-password>")):
                raise Neo4jConfigurationError(f"Fill in {name} locally.")
        parsed = urlsplit(self.uri.get_secret_value())
        if parsed.scheme not in {"neo4j+s", "neo4j", "bolt+s", "bolt"} or not parsed.hostname:
            raise Neo4jConfigurationError("NEO4J_URI has an unsupported scheme or missing host.")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise Neo4jConfigurationError("NEO4J_URI must not contain embedded credentials, query or fragment.")
        if self.ca_file is not None and not self.ca_file.is_file():
            raise Neo4jConfigurationError("SSL_CERT_FILE must point to an existing trusted CA bundle.")
        return self


def load_neo4j_settings(env_file: Path | None = None) -> Neo4jSettings:
    """Environment takes precedence; interpolation is disabled to preserve passwords."""
    from dotenv import dotenv_values

    local = dotenv_values(env_file, interpolate=False) if env_file is not None and env_file.is_file() else {}
    fields = {}
    for name in ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD", "NEO4J_DATABASE", "SSL_CERT_FILE"):
        fields[name] = os.environ[name] if name in os.environ else local.get(name)
    missing = [name for name in ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD") if not fields[name]]
    if missing:
        raise Neo4jConfigurationError("Missing configuration: " + ", ".join(missing))
    return Neo4jSettings(
        uri=SecretStr(fields["NEO4J_URI"]), username=SecretStr(fields["NEO4J_USERNAME"]),
        password=SecretStr(fields["NEO4J_PASSWORD"]), database=fields["NEO4J_DATABASE"] or "neo4j",
        ca_file=Path(fields["SSL_CERT_FILE"]) if fields["SSL_CERT_FILE"] else None,
    )
