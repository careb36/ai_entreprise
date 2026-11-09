import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from backend.app.schemas.sandbox import (
    SandboxExecutionRequest,
    SandboxExecutionResponse,
    SandboxHealthResult,
)
from backend.app.services.sandbox_manager import EphemeralSandboxManager
from backend.app.main import app

client = TestClient(app)

def test_sandbox_schemas():
    req = SandboxExecutionRequest(
        code="print('hello')",
        filename="test.py",
        timeout_seconds=15,
        mode="k8s"
    )
    assert req.code == "print('hello')"
    assert req.timeout_seconds == 15
    assert req.mode == "k8s"

    res = SandboxExecutionResponse(
        sandbox_id="sandbox-12345678",
        success=True,
        execution_time_seconds=0.45,
        stdout="hello\n",
        stderr="",
        health={"status": "ok", "sandbox": "healthy"},
        timed_out=False
    )
    assert res.sandbox_id == "sandbox-12345678"
    assert res.success is True
    assert res.health["status"] == "ok"


def test_sandbox_manager_direct_fallback_valid_code():
    manager = EphemeralSandboxManager()
    req = SandboxExecutionRequest(
        code="def add(a, b):\n    return a + b\n",
        filename="math_mod.py",
        timeout_seconds=10,
        mode="k8s"
    )
    response = manager.run_sandbox_validation(req)
    assert response.success is True
    assert response.timed_out is False
    assert response.sandbox_id.startswith("sandbox-")
    assert response.execution_time_seconds >= 0.0
    assert response.health is not None


def test_sandbox_manager_direct_fallback_invalid_code():
    manager = EphemeralSandboxManager()
    req = SandboxExecutionRequest(
        code="def broken_func(:\n    return True",
        filename="broken.py",
        timeout_seconds=10,
        mode="docker"
    )
    response = manager.run_sandbox_validation(req)
    assert response.success is False
    assert "SyntaxError" in response.stderr


@patch("kubernetes.client.BatchV1Api")
@patch("kubernetes.client.CoreV1Api")
@patch("kubernetes.config.load_kube_config")
def test_sandbox_manager_k8s_flow(mock_load_kube, mock_core_api, mock_batch_api):
    mock_batch_inst = MagicMock()
    mock_batch_api.return_value = mock_batch_inst
    
    mock_core_inst = MagicMock()
    mock_core_api.return_value = mock_core_inst

    mock_pod = MagicMock()
    mock_pod.metadata.name = "sandbox-pod-1"
    mock_pod.status.pod_ip = "10.244.0.15"
    mock_pod.status.phase = "Running"
    mock_core_inst.list_namespaced_pod.return_value.items = [mock_pod]

    manager = EphemeralSandboxManager()
    req = SandboxExecutionRequest(
        code="x = 10",
        filename="script.py",
        timeout_seconds=5,
        mode="k8s"
    )

    with patch.object(manager, "_query_sandbox_http") as mock_http:
        mock_http.return_value = ({"status": "ok", "sandbox": "healthy"}, "x = 10 output", "", True)
        res = manager.run_sandbox_validation(req)

        assert res.success is True
        assert res.stdout == "x = 10 output"
        # Ensure job creation and deletion (teardown) were called
        mock_batch_inst.create_namespaced_job.assert_called_once()
        mock_batch_inst.delete_namespaced_job.assert_called_once()


@patch("docker.from_env")
def test_sandbox_manager_docker_flow(mock_docker):
    mock_client = MagicMock()
    mock_container = MagicMock()
    mock_docker.return_value = mock_client
    mock_client.containers.run.return_value = mock_container
    mock_container.attrs = {
        'NetworkSettings': {
            'Ports': {
                '8080/tcp': [{'HostPort': '38080'}]
            }
        }
    }
    mock_client.containers.get.return_value = mock_container

    manager = EphemeralSandboxManager()
    req = SandboxExecutionRequest(
        code="y = 20",
        filename="script.py",
        timeout_seconds=5,
        mode="docker"
    )

    with patch.object(manager, "_query_sandbox_http") as mock_http:
        mock_http.return_value = ({"status": "ok", "sandbox": "healthy"}, "y = 20 output", "", True)
        res = manager.run_sandbox_validation(req)

        assert res.success is True
        assert res.stdout == "y = 20 output"
        mock_container.stop.assert_called_once()
        mock_container.remove.assert_called_once()


def test_sandbox_api_endpoint_success():
    payload = {
        "code": "def hello():\n    return 'world'\n",
        "filename": "hello.py",
        "timeout_seconds": 10,
        "mode": "k8s"
    }
    response = client.post("/api/v1/sandbox/validate", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "sandbox_id" in data
    assert data["timed_out"] is False


def test_sandbox_api_endpoint_syntax_error():
    payload = {
        "code": "def hello()",
        "filename": "hello_err.py",
        "timeout_seconds": 5,
        "mode": "docker"
    }
    response = client.post("/api/v1/sandbox/validate", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is False
    assert "SyntaxError" in data["stderr"]
