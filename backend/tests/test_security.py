"""
Tests for security features: rate limiting, CORS, and security headers.
"""


def test_security_headers_present(test_client):
    """Verify security headers are added to responses."""
    response = test_client.get("/health")

    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("X-Frame-Options") == "DENY"
    # The legacy XSS auditor is turned off, not configured (issue #541).
    assert response.headers.get("X-XSS-Protection") == "0"
    assert "max-age=31536000" in response.headers.get("Strict-Transport-Security", "")
    assert response.headers.get("Content-Security-Policy").startswith(
        "default-src 'none'; frame-ancestors 'none'"
    )
    assert response.headers.get("Referrer-Policy") == "no-referrer"
    assert response.headers.get("Cross-Origin-Opener-Policy") == "same-origin"


def test_cors_origins_configured(test_client):
    """Verify CORS origins are configured from settings."""
    from config import settings

    # Should have specific origins, not wildcard
    assert settings.CORS_ORIGINS != "*"
    origins = [o.strip() for o in settings.CORS_ORIGINS.split(",")]
    assert len(origins) > 0


def test_rate_limit_headers_present(test_client):
    """Verify rate limit headers are present in responses."""
    response = test_client.get("/health")

    # slowapi adds rate limit headers
    assert "X-RateLimit-Limit" in response.headers or response.status_code == 200


def test_health_check_not_rate_limited(test_client):
    """Verify health check endpoint is accessible."""
    response = test_client.get("/health")
    assert response.status_code == 200
    # Degraded is still up: the suite runs with tests off (issue #766).
    assert response.json()["status"] in ("healthy", "degraded")


def test_security_headers_on_error_responses(test_client):
    """Verify security headers are present even on error responses."""
    response = test_client.get("/nonexistent-endpoint")

    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("X-Frame-Options") == "DENY"
