"""
Basic smoke tests for Document OCR API endpoints.

These tests verify that endpoints are accessible and handle requests correctly.
For full integration testing, use actual document files.
"""

import json
import pytest
from app import app


@pytest.fixture
def client():
    """Create a test client for the Flask app."""
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client


class TestHealthEndpoint:
    """Test the health check endpoint."""

    def test_health_check(self, client):
        """GET / should return health status."""
        response = client.get('/')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert 'status' in data
        assert data['status'] == 'healthy'


class TestAPIDocumentation:
    """Test API documentation endpoints."""

    def test_swagger_docs_available(self, client):
        """Swagger documentation should be available."""
        response = client.get('/api-docs')
        assert response.status_code == 200

    def test_apispec_available(self, client):
        """API spec should be available."""
        response = client.get('/apispec_1.json')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert 'swagger' in data or 'openapi' in data


class TestPassportEndpoints:
    """Test passport processing endpoints."""

    def test_passport_endpoint_exists(self, client):
        """Passport endpoint should exist but return error without file."""
        response = client.post('/api/passport')
        assert response.status_code == 400
        data = json.loads(response.data)
        assert data['success'] is False
        assert 'No file provided' in data['message']

    def test_scan_passport_endpoint_exists(self, client):
        """Legacy scan-passport endpoint should exist."""
        response = client.post('/api/scan-passport')
        assert response.status_code == 400
        data = json.loads(response.data)
        assert data['success'] is False


class TestNINEndpoint:
    """Test NIN processing endpoint."""

    def test_nin_endpoint_exists(self, client):
        """NIN endpoint should exist but return error without file."""
        response = client.post('/api/nin')
        assert response.status_code == 400
        data = json.loads(response.data)
        assert data['success'] is False
        assert 'No file provided' in data['message']


class TestBankStatementEndpoint:
    """Test bank statement processing endpoint."""

    def test_bank_statement_endpoint_exists(self, client):
        """Bank statement endpoint should exist but return error without file."""
        response = client.post('/api/bank-statement')
        assert response.status_code == 400
        data = json.loads(response.data)
        assert data['success'] is False
        assert 'No file provided' in data['message']


class TestCountryEndpoints:
    """Test country metadata discovery endpoints."""

    def test_countries_endpoint_lists_registered_countries(self, client):
        """GET /api/countries should list Nigeria and Ghana metadata."""
        response = client.get('/api/countries')
        assert response.status_code == 200
        data = json.loads(response.data)
        codes = {country['country_code'] for country in data['countries']}
        assert {'NGA', 'GHA'}.issubset(codes)

    def test_single_country_endpoint_returns_supported_ids(self, client):
        """GET /api/countries/NGA should include local Nigerian IDs."""
        response = client.get('/api/countries/NGA')
        assert response.status_code == 200
        data = json.loads(response.data)
        docs = {doc['code'] for doc in data['country']['supported_identity_documents']}
        assert 'VOTER_CARD' in docs
        assert 'DRIVERS_LICENSE' in docs

    def test_single_country_endpoint_rejects_unknown_country(self, client):
        """Unknown country codes should return 404."""
        response = client.get('/api/countries/XYZ')
        assert response.status_code == 404


class TestWebhookForwarderEndpoint:
    """Test webhook forwarder endpoint."""

    def test_webhook_forwarder_endpoint_exists(self, client):
        """Webhook forwarder endpoint should exist."""
        response = client.post(
            '/api/webhooks/forward',
            data=json.dumps({'test': 'payload'}),
            content_type='application/json'
        )
        # Should fail due to missing FORWARDER_SECRET or validation
        assert response.status_code in (200, 500)

    def test_webhook_forward_with_empty_body(self, client):
        """Webhook forwarder should handle empty body."""
        response = client.post('/api/webhooks/forward', data=b'')
        assert response.status_code in (200, 500)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

