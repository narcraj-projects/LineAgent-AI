"""Explicit hosted-demo boundary; provider secrets never enter browser code."""

import base64
import binascii
import os
import re
import secrets

from pydantic import SecretStr

from src.schemas import Contract


class DeploymentSettings(Contract):
    enabled: bool
    domain: str | None = None
    username: SecretStr | None = None
    password: SecretStr | None = None

    @property
    def ready(self) -> bool:
        return bool(self.domain and self.username and self.password
                    and len(self.password.get_secret_value()) >= 16)

    def authorized(self, header: str) -> bool:
        try:
            scheme, encoded = header.split(' ', 1)
            if scheme.lower() != 'basic':
                return False
            username, password = base64.b64decode(encoded, validate=True).decode('utf-8').split(':', 1)
        except (ValueError, UnicodeError, binascii.Error):
            return False
        if not self.username or not self.password:
            return False
        user_ok = secrets.compare_digest(username.encode(), self.username.get_secret_value().encode())
        pass_ok = secrets.compare_digest(password.encode(), self.password.get_secret_value().encode())
        return user_ok and pass_ok


def load_deployment_settings() -> DeploymentSettings:
    domain = os.environ.get('RAILWAY_PUBLIC_DOMAIN') or os.environ.get('LINEMEDIC_PUBLIC_DOMAIN') or None
    if domain and (len(domain) > 253 or not re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?', domain)):
        raise ValueError('Set a hostname without a URL scheme, path or port.')
    username = os.environ.get('LINEMEDIC_DEMO_USERNAME')
    password = os.environ.get('LINEMEDIC_DEMO_PASSWORD')
    return DeploymentSettings(enabled=bool(domain or os.environ.get('RAILWAY_ENVIRONMENT_ID')
        or os.environ.get('LINEMEDIC_DEPLOYED') == 'true'), domain=domain,
        username=SecretStr(username) if username else None,
        password=SecretStr(password) if password else None)
