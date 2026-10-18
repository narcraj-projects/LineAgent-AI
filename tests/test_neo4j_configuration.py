"""Configuration and error redaction tests; no live service calls in this module."""

import json
import os
from pathlib import Path
import sys

import pytest
from pydantic import SecretStr, ValidationError

from scripts import check_neo4j
from src.adapters.neo4j_settings import Neo4jConfigurationError, Neo4jSettings, load_neo4j_settings


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD", "NEO4J_DATABASE", "SSL_CERT_FILE"):
        monkeypatch.delenv(name, raising=False)


def test_missing_configuration_is_explicit() -> None:
    with pytest.raises(Neo4jConfigurationError, match="NEO4J_URI"):
        load_neo4j_settings()


def test_file_loading_preserves_literal_password_and_masks_repr(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text('NEO4J_URI=neo4j+s://synthetic.invalid\nNEO4J_USERNAME=fixture-user\nNEO4J_PASSWORD="literal-${HOME}-fixture"\n', encoding="utf-8")
    settings = load_neo4j_settings(path)
    assert settings.password.get_secret_value() == "literal-${HOME}-fixture"
    assert "fixture-user" not in repr(settings)
    assert "literal-" not in repr(settings)
    assert settings.database == "neo4j"


def test_environment_takes_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / ".env"
    path.write_text("NEO4J_URI=bolt://synthetic.invalid\nNEO4J_USERNAME=fixture\nNEO4J_PASSWORD=file-fixture\n", encoding="utf-8")
    monkeypatch.setenv("NEO4J_PASSWORD", "environment-fixture")
    assert load_neo4j_settings(path).password.get_secret_value() == "environment-fixture"


@pytest.mark.parametrize("uri", ["neo4j+s://YOUR-INSTANCE.databases.neo4j.io", "https://synthetic.invalid", "neo4j+s://user:password@synthetic.invalid"])
def test_placeholder_or_invalid_uri_is_rejected(uri: str) -> None:
    with pytest.raises(ValidationError):
        Neo4jSettings(uri=SecretStr(uri), username=SecretStr("fixture"), password=SecretStr("fixture"))


def test_connection_failure_does_not_print_raw_error(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    settings = Neo4jSettings(uri=SecretStr("bolt://synthetic.invalid"), username=SecretStr("fixture"), password=SecretStr("fixture"))
    monkeypatch.setattr(sys, "argv", ["check_neo4j"])
    monkeypatch.setattr(check_neo4j, "load_neo4j_settings", lambda path: settings)

    def fail(secret_settings: Neo4jSettings) -> None:
        raise RuntimeError("never-print-this-credential")

    monkeypatch.setattr(check_neo4j, "check_connection", fail)
    with pytest.raises(SystemExit) as exit_info:
        check_neo4j.main()
    assert exit_info.value.code == 1
    output = capsys.readouterr()
    assert "never-print-this-credential" not in output.out + output.err
    assert json.loads(output.out)["status"] == "connection_failed"


def test_cli_uses_configured_trusted_ca_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    ca_file = tmp_path / "fixture-ca.pem"
    ca_file.write_text("Unit-test path fixture only; no TLS connection.", encoding="utf-8")
    settings = Neo4jSettings(uri=SecretStr("neo4j+s://synthetic.invalid"), username=SecretStr("fixture"), password=SecretStr("fixture"), ca_file=ca_file)
    monkeypatch.setattr(sys, "argv", ["check_neo4j"])
    monkeypatch.setattr(check_neo4j, "load_neo4j_settings", lambda path: settings)

    def probe(secret_settings: Neo4jSettings) -> None:
        assert os.environ["SSL_CERT_FILE"] == str(ca_file)

    monkeypatch.setattr(check_neo4j, "check_connection", probe)
    check_neo4j.main()
    assert json.loads(capsys.readouterr().out)["status"] == "connected"
