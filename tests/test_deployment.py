"""Hosted boundary tests; credentials are explicit synthetic test fixtures."""

from fastapi.testclient import TestClient
import pytest

from src.api import create_api, read_evaluation
from src.deployment import load_deployment_settings
from pathlib import Path


def configure(monkeypatch):
    monkeypatch.setenv('LINEMEDIC_DEPLOYED', 'true')
    monkeypatch.setenv('LINEMEDIC_PUBLIC_DOMAIN', 'synthetic-demo.example')
    monkeypatch.setenv('LINEMEDIC_DEMO_USERNAME', 'fixture')
    monkeypatch.setenv('LINEMEDIC_DEMO_PASSWORD', 'synthetic-fixture-password')


def test_hosted_health_is_liveness_only_and_application_requires_password(monkeypatch):
    configure(monkeypatch)
    with TestClient(create_api(), base_url='https://synthetic-demo.example') as client:
        health = client.get('/api/health')
        assert health.status_code == 200 and 'provider readiness' in health.json()['scope']
        for path in ('/', '/api/catalog', '/api/evaluation', '/docs'):
            response = client.get(path)
            assert response.status_code == 401
            assert response.headers['www-authenticate'].startswith('Basic ')
        assert client.get('/api/catalog', auth=('fixture', 'wrong')).status_code == 401
        response = client.get('/api/catalog', auth=('fixture', 'synthetic-fixture-password'))
        assert response.status_code == 200 and response.json()['synthetic'] is True
        assert client.post('/api/connection').status_code == 401
        response = client.post('/api/connection', json={}, auth=('fixture', 'synthetic-fixture-password'),
            headers={'Origin': 'https://foreign.example'})
        assert response.status_code == 403
        assert client.get('/api/status', auth=('fixture', 'synthetic-fixture-password'),
            headers={'host': 'foreign.example'}).status_code == 400


@pytest.mark.parametrize('password', ['', 'too-short'])
def test_hosted_missing_or_weak_credentials_fail_closed(monkeypatch, password):
    configure(monkeypatch)
    monkeypatch.setenv('LINEMEDIC_DEMO_PASSWORD', password)
    with TestClient(create_api()) as client:
        assert client.get('/api/health').status_code == 503
        assert client.get('/api/catalog').status_code == 503


def test_missing_domain_and_malformed_auth_fail_closed(monkeypatch):
    configure(monkeypatch)
    settings = load_deployment_settings()
    assert not settings.authorized('Basic not-valid-base64!!!')
    assert not settings.authorized('Bearer ignored')
    monkeypatch.delenv('LINEMEDIC_PUBLIC_DOMAIN')
    with TestClient(create_api()) as client:
        assert client.get('/api/health').status_code == 503
    monkeypatch.setenv('LINEMEDIC_PUBLIC_DOMAIN', 'https://invalid.example/path')
    with pytest.raises(ValueError, match='hostname'):
        load_deployment_settings()


def test_packaged_evaluation_matches_saved_measurements(tmp_path):
    import shutil
    root = Path(__file__).resolve().parents[1]
    shutil.copytree(root / 'evals/published', tmp_path / 'evals/published')
    report = read_evaluation(tmp_path)
    assert report.status == 'measured' and report.pdf_backed is True
    assert [mode.passed for mode in report.modes] == [15, 30, 30, 30]
    assert all(mode.cost_usd is None for mode in report.modes)
