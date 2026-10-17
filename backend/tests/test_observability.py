from metrics import record_http_request


def test_metrics_endpoint_exposes_http_request_metrics(test_client):
    response = test_client.get("/health")
    assert response.status_code == 200

    metrics = test_client.get("/metrics")

    assert metrics.status_code == 200
    assert "text/plain" in metrics.headers["content-type"]
    assert 'fabrique_http_requests_total{method="GET",route="/health",status="200"}' in metrics.text
    assert 'route="/metrics"' not in metrics.text


def test_http_metrics_record_server_errors(test_client):
    record_http_request("POST", "/example", 500, 0.25)

    metrics = test_client.get("/metrics")

    assert 'fabrique_http_requests_total{method="POST",route="/example",status="500"}' in metrics.text
