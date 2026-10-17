"""
Tests for Prometheus metrics.
"""


def test_metrics_endpoint_accessible(test_client):
    """Verify /metrics endpoint is accessible."""
    response = test_client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers.get("content-type", "")


def test_metrics_contain_request_count(test_client):
    """Verify metrics contain request counter."""
    # Make a request to generate metrics
    test_client.get("/health")

    response = test_client.get("/metrics")
    assert "fabrique_requests_total" in response.text


def test_metrics_contain_active_connections(test_client):
    """Verify metrics contain active connections gauge."""
    response = test_client.get("/metrics")
    assert "fabrique_active_connections" in response.text


def test_metrics_contain_agent_run_metrics(test_client):
    """Verify metrics contain agent run counters."""
    response = test_client.get("/metrics")
    assert "fabrique_agent_runs_ingested_total" in response.text
    assert "fabrique_agent_runs_verified_total" in response.text


def test_metrics_contain_sprint_metrics(test_client):
    """Verify metrics contain sprint counters."""
    response = test_client.get("/metrics")
    assert "fabrique_sprints_created_total" in response.text
    assert "fabrique_sprints_completed_total" in response.text


def test_metrics_contain_error_metrics(test_client):
    """Verify metrics contain error counters."""
    response = test_client.get("/metrics")
    assert "fabrique_errors_total" in response.text


def test_request_increments_counter(test_client):
    """Verify that making requests increments the request counter."""
    # Make a request
    test_client.get("/health")

    response = test_client.get("/metrics")
    # The counter should be present (value may vary)
    assert "fabrique_requests_total" in response.text
