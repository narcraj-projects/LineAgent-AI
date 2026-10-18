"""Read-only public Fireworks model catalog; credentials remain server-side."""
from pathlib import Path
import ssl
from typing import Any

import httpx
from src.adapters.base import AdapterUnavailable
from src.schemas import Contract, NonEmpty


class CatalogEntry(Contract):
    model: NonEmpty
    serverless: bool
    kind: str
    context_length: int | None


class FireworksCatalog:
    def __init__(self, key: str, ca_file: Path | None = None, client: httpx.Client | None = None) -> None:
        self._key = key
        self._client = client or httpx.Client(timeout=30, follow_redirects=False,
            verify=ssl.create_default_context(cafile=str(ca_file)) if ca_file else True)

    def models(self) -> tuple[CatalogEntry, ...]:
        rows: dict[str, CatalogEntry] = {}
        token: str | None = None
        try:
            for _ in range(20):
                parameters: dict[str, Any] = {"pageSize": 200}
                if token:
                    parameters["pageToken"] = token
                response = self._client.get("https://api.fireworks.ai/v1/accounts/fireworks/models",
                    headers={"Authorization": f"Bearer {self._key}"}, params=parameters)
                response.raise_for_status()
                body = response.json()
                if not isinstance(body.get("models"), list):
                    raise ValueError("Invalid model list")
                for item in body["models"]:
                    entry = CatalogEntry(model=item["name"], serverless=item.get("supportsServerless",False),
                        kind=item.get("kind",""), context_length=item.get("contextLength") or None)
                    if not entry.model.startswith("accounts/fireworks/models/"):
                        raise ValueError("Unexpected public model identity")
                    if entry.model in rows and rows[entry.model] != entry:
                        raise ValueError("Conflicting model catalog")
                    rows[entry.model] = entry
                next_token = body.get("nextPageToken")
                if not next_token:
                    return tuple(rows[key] for key in sorted(rows))
                if not isinstance(next_token,str) or next_token == token:
                    raise ValueError("Invalid catalog pagination")
                token = next_token
            raise ValueError("Catalog pagination exceeded bound")
        except Exception:
            raise AdapterUnavailable("Fireworks catalog unavailable or invalid; provider details withheld.") from None

    def close(self) -> None:
        self._client.close()