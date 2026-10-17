import json
import os
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_generated_openapi_matches_fastapi_app(tmp_path):
    generated = tmp_path / "openapi.yaml"
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT / "backend"), str(ROOT / "backend" / "app")]
    )

    subprocess.run(
        [
            sys.executable,
            str(ROOT / "backend" / "scripts" / "generate_openapi.py"),
            "--output",
            str(generated),
        ],
        check=True,
        cwd=ROOT,
        env=env,
    )

    schema = json.loads(generated.read_text(encoding="utf-8"))
    checked_in_schema = (ROOT / "openapi.yaml").read_text(encoding="utf-8")
    assert generated.read_text(encoding="utf-8") == checked_in_schema
    assert schema["openapi"].startswith("3.")
    assert "/v1/agent_runs" in schema["paths"]
    assert "/v1/glassroom/stream" in schema["paths"]


def test_api_guide_has_an_example_for_every_operation():
    schema = json.loads((ROOT / "openapi.yaml").read_text(encoding="utf-8"))
    guide = (ROOT / "docs" / "API.md").read_text(encoding="utf-8")
    documented = {
        (method.lower(), path)
        for method, path in re.findall(
            r"^# (GET|POST|PATCH|PUT|DELETE) (\S+)", guide, re.MULTILINE
        )
    }
    operations = {
        (method, path)
        for path, methods in schema["paths"].items()
        for method in methods
    }

    assert documented == operations
    assert "NATS event contracts" in guide
    assert "HTTP status and error meanings" in guide
