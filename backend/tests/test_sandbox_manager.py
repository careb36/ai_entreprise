"""The ephemeral sandbox manager: schemas, the syntax-check fallback, and the
Kubernetes and Docker flows against fakes of their client libraries."""

import importlib.util
import sys
import types
from unittest.mock import MagicMock, patch

import pytest
from services.sandbox_manager import EphemeralSandboxManager
from services.sandbox_schemas import (
    SandboxExecutionRequest,
    SandboxExecutionResponse,
)


def _fake_module(name: str, **attributes) -> types.ModuleType:
    module = types.ModuleType(name)
    for key, value in attributes.items():
        setattr(module, key, value)
    return module


@pytest.fixture(autouse=True)
def client_libraries(monkeypatch):
    """The manager imports kubernetes and docker lazily; neither is a backend
    dependency, so stand-ins that fail as an unconfigured host does are
    installed when the real ones are missing; a test patches what it needs."""
    if importlib.util.find_spec("kubernetes") is None:
        k8s_client = _fake_module(
            "kubernetes.client", BatchV1Api=MagicMock(), CoreV1Api=MagicMock()
        )
        k8s_config = _fake_module(
            "kubernetes.config",
            load_incluster_config=MagicMock(side_effect=Exception("not in a cluster")),
            load_kube_config=MagicMock(side_effect=Exception("no kubeconfig")),
        )
        k8s = _fake_module("kubernetes", client=k8s_client, config=k8s_config)
        monkeypatch.setitem(sys.modules, "kubernetes", k8s)
        monkeypatch.setitem(sys.modules, "kubernetes.client", k8s_client)
        monkeypatch.setitem(sys.modules, "kubernetes.config", k8s_config)
    if importlib.util.find_spec("docker") is None:
        monkeypatch.setitem(
            sys.modules,
            "docker",
            _fake_module(
                "docker", from_env=MagicMock(side_effect=Exception("no docker"))
            ),
        )


def test_sandbox_schemas():
    req = SandboxExecutionRequest(
        code="print('hello')", filename="test.py", timeout_seconds=15, mode="k8s"
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
        timed_out=False,
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
        mode="k8s",
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
        mode="docker",
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
        code="x = 10", filename="script.py", timeout_seconds=5, mode="k8s"
    )

    with patch.object(manager, "_query_sandbox_http") as mock_http:
        mock_http.return_value = (
            {"status": "ok", "sandbox": "healthy"},
            "x = 10 output",
            "",
            True,
        )
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
        "NetworkSettings": {"Ports": {"8080/tcp": [{"HostPort": "38080"}]}}
    }
    mock_client.containers.get.return_value = mock_container

    manager = EphemeralSandboxManager()
    req = SandboxExecutionRequest(
        code="y = 20", filename="script.py", timeout_seconds=5, mode="docker"
    )

    with patch.object(manager, "_query_sandbox_http") as mock_http:
        mock_http.return_value = (
            {"status": "ok", "sandbox": "healthy"},
            "y = 20 output",
            "",
            True,
        )
        res = manager.run_sandbox_validation(req)

        assert res.success is True
        assert res.stdout == "y = 20 output"
        mock_container.stop.assert_called_once()
        mock_container.remove.assert_called_once()
