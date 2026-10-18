"""Read-only connection probe. Never prints settings, credentials or raw errors."""

import argparse
import json
import os
from pathlib import Path
import sys

from src.adapters.neo4j_settings import Neo4jSettings, load_neo4j_settings


def check_connection(settings: Neo4jSettings) -> None:
    from neo4j import GraphDatabase, Query

    with GraphDatabase.driver(
        settings.uri.get_secret_value(),
        auth=(settings.username.get_secret_value(), settings.password.get_secret_value()),
        connection_timeout=5.0, connection_acquisition_timeout=10.0,
        max_transaction_retry_time=0.0,
    ) as driver:
        driver.verify_connectivity()
        with driver.session(database=settings.database, default_access_mode="READ") as session:
            record = session.run(Query("RETURN 1 AS ok", timeout=10.0)).single()
            if record is None or record["ok"] != 1:
                raise RuntimeError("Read-only connection probe failed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(__file__).resolve().parents[1] / ".env")
    args = parser.parse_args()
    try:
        settings = load_neo4j_settings(args.env_file)
    except Exception:
        print(json.dumps({"status": "configuration_error", "detail": "Check Neo4j fields in the local environment or .env. Values withheld."}))
        sys.exit(1)
    try:
        # CLI process only: explicitly selected CA bundle, with TLS verification
        # still required by the neo4j+s URI. Never use an unverified SSL context.
        if settings.ca_file is not None:
            os.environ["SSL_CERT_FILE"] = str(settings.ca_file)
        check_connection(settings)
    except Exception as exc:
        # Exception text may contain hosts or credentials; report only its class.
        print(json.dumps({"status": "connection_failed", "error_type": type(exc).__name__, "detail": "Provider error details withheld."}))
        sys.exit(1)
    print(json.dumps({"status": "connected", "read_probe": "passed", "database_modified": False}))


if __name__ == "__main__":
    main()
