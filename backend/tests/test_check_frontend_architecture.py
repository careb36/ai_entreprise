"""The frontend's dependency rule between layers (issue #519).

``scripts/check_frontend_architecture.py`` reads the imports of
``frontend/src`` and holds them to ``docs/architecture/frontend.json``. These
tests run it on the repository (it must pass with today's code thanks to its
allowlist) and on small trees of their own: an import downwards, one
upwards, a component importing a page, the network outside the listed
modules, an alias, a dynamic import, the tests' exemption and its limit, an
allowlisted violation, and a stale entry that ``--update`` drops.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "check_frontend_architecture.py"
_spec = importlib.util.spec_from_file_location("check_frontend_architecture", SCRIPT)
assert _spec and _spec.loader
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)

LAYERS = [
    {"name": "foundation", "paths": ["i18n"]},
    {"name": "services", "paths": ["services"]},
    {"name": "state", "paths": ["hooks"]},
    {"name": "components", "paths": ["components"]},
    {"name": "pages", "paths": ["pages"]},
    {"name": "app", "paths": ["App.tsx"]},
]
BASE = {
    "i18n/index.ts": "export const t = (k: string) => k\n",
    "services/api/client.ts": (
        "// fetch('/not/a/call') in a comment\n"
        "export const get = (u: string) => fetch(`${u}?x=1`).then((r) => r.json())\n"
    ),
    "services/api.ts": "export * from './api/client'\n",
    "hooks/useThing.ts": "import { get } from '../services/api'\nexport const useThing = () => get('/a')\n",
    "components/Card.tsx": (
        "import { useThing } from '../hooks/useThing'\n"
        "import {\n  t,\n} from '../i18n'\n"
        "export const Card = () => { useThing(); return t('x') }\n"
    ),
    "pages/Home.tsx": "import { Card } from '../components/Card'\nexport const Home = Card\n",
    "App.tsx": "import { Home } from './pages/Home'\nimport 'react'\nexport default Home\n",
}


def _tree(tmp_path: Path, extra: dict[str, str] | None = None) -> Path:
    src = tmp_path / "src"
    for rel, text in {**BASE, **(extra or {})}.items():
        path = src / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return src


def _data(allowlist: list[str] | None = None, **extra) -> dict:
    return {
        "layers": LAYERS,
        "aliases": {},
        "tests": {
            "paths": ["test"],
            "patterns": ["*.test.ts", "*.test.tsx", "__tests__"],
        },
        "network": {"modules": ["services/api/client.ts"]},
        "allowlist": allowlist or [],
        **extra,
    }


def test_the_repository_keeps_to_the_rule():
    result = subprocess.run(
        [sys.executable, "-I", str(SCRIPT)], check=False, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    analysis = check.analyse(check.load())
    assert analysis["problems"] == []
    assert analysis["stale"] == [] and analysis["network_stale"] == []
    # The network is used by the listed services modules only.
    assert all(key.startswith("services/") for key in analysis["network"])
    # The parser finds the imports: the routes (app/AppRoutes.tsx) reach the pages.
    assert any(
        src == "app/AppRoutes.tsx" and dst.startswith("pages/")
        for src, dst in analysis["edges"]
    )


def test_imports_downwards_and_within_a_layer_pass(tmp_path):
    src = _tree(tmp_path)
    result = check.analyse(_data(), src)
    assert result["problems"] == []
    edges = set(result["edges"])
    assert ("components/Card.tsx", "i18n/index.ts") in edges  # a multi-line import
    assert ("services/api.ts", "services/api/client.ts") in edges  # export … from
    assert ("App.tsx", "pages/Home.tsx") in edges
    # A bare specifier is a package; a commented-out call is no use of fetch.
    assert result["network"] == {"services/api/client.ts": ["fetch"]}


def test_an_upward_import_fails(tmp_path):
    src = _tree(
        tmp_path,
        {
            "services/api/client.ts": "import { useThing } from '../../hooks/useThing'\nexport const get = useThing\n"
        },
    )
    result = check.analyse(_data(network={"modules": []}), src)
    assert result["new"] == ["services/api/client.ts -> hooks/useThing.ts"]
    assert any("services may not import state" in p for p in result["problems"])


def test_a_component_may_not_import_a_page(tmp_path):
    src = _tree(
        tmp_path,
        {
            "components/Badge.tsx": "import type { Home } from '../pages/Home'\nexport type B = typeof Home\n"
        },
    )
    result = check.analyse(_data(), src)
    assert result["new"] == ["components/Badge.tsx -> pages/Home.tsx"]
    assert "components may not import pages" in result["problems"][0]


def test_the_network_outside_the_listed_modules_fails(tmp_path):
    src = _tree(
        tmp_path,
        {
            "components/Live.tsx": (
                "const url = 'new WebSocket(x)'\n"  # a string, not code
                "export const open = () => new EventSource(url)\n"
                "export const load = () => window.fetch(url)\n"
                "export const refetch = (q: { refetch(): void }) => q.refetch()\n"
            ),
        },
    )
    result = check.analyse(_data(), src)
    assert result["new"] == [
        "components/Live.tsx: EventSource",
        "components/Live.tsx: fetch",
    ]
    assert result["network"]["components/Live.tsx"] == ["EventSource", "fetch"]


def test_the_alias_resolves_to_src(tmp_path):
    src = _tree(
        tmp_path,
        {
            "hooks/useAlias.ts": "import { Card } from '@/components/Card'\nexport const x = Card\n"
        },
    )
    result = check.analyse(_data(aliases={"@/": ""}), src)
    assert result["new"] == ["hooks/useAlias.ts -> components/Card.tsx"]
    # Without the alias it is a package, and no edge.
    assert check.analyse(_data(), src)["new"] == []


def test_a_dynamic_import_is_an_edge(tmp_path):
    src = _tree(
        tmp_path,
        {"components/Lazy.tsx": "export const load = () => import('../pages/Home')\n"},
    )
    result = check.analyse(_data(), src)
    assert result["new"] == ["components/Lazy.tsx -> pages/Home.tsx"]


def test_an_import_that_resolves_to_no_file_fails(tmp_path):
    src = _tree(
        tmp_path,
        {
            "components/Broken.tsx": "import { x } from './Missing'\nexport const y = x\n"
        },
    )
    problems = check.analyse(_data(), src)["problems"]
    assert problems == [
        "components/Broken.tsx: imports './Missing', which resolves to no file"
    ]


def test_tests_are_exempt_but_never_imported(tmp_path):
    src = _tree(
        tmp_path,
        {
            # A test imports any layer and stubs the network.
            "services/api.test.ts": "import { Home } from '../pages/Home'\nfetch('/x')\nexport { Home }\n",
            "test/fixtures.ts": "export const FIXTURE = 1\n",
            # A module that ships may not import the test support.
            "components/Uses.tsx": "import { FIXTURE } from '../test/fixtures'\nexport const z = FIXTURE\n",
        },
    )
    result = check.analyse(_data(), src)
    assert "services/api.test.ts" in result["tests"]
    assert result["new"] == ["components/Uses.tsx -> test/fixtures.ts"]
    assert "imports a test" in result["problems"][0]


def test_a_file_in_no_layer_fails(tmp_path):
    src = _tree(tmp_path, {"utils/x.ts": "export const x = 1\n"})
    problems = check.analyse(_data(), src)["problems"]
    assert problems == ["utils/x.ts: in no layer; add its folder to frontend.json"]


def test_the_allowlist_only_shrinks(tmp_path):
    upward = "components/Badge.tsx -> pages/Home.tsx"
    src = _tree(
        tmp_path,
        {
            "components/Badge.tsx": "import { Home } from '../pages/Home'\nexport const B = Home\n"
        },
    )
    result = check.analyse(_data(allowlist=[upward, "hooks/useThing.ts: fetch"]), src)
    assert result["problems"] == []
    assert result["violations"] == [upward]
    assert result["stale"] == ["hooks/useThing.ts: fetch"]

    data_path = tmp_path / "frontend.json"
    data_path.write_text(
        json.dumps(_data(allowlist=[upward, "hooks/useThing.ts: fetch"]))
    )
    assert check.main([], data_path, src) == 0
    assert check.main(["--update"], data_path, src) == 0
    assert json.loads(data_path.read_text())["allowlist"] == [upward]


def test_a_network_module_that_stopped_using_it_is_dropped(tmp_path):
    src = _tree(tmp_path, {"services/api/stream.ts": "export const none = 1\n"})
    data_path = tmp_path / "frontend.json"
    data_path.write_text(
        json.dumps(
            _data(
                network={
                    "modules": ["services/api/client.ts", "services/api/stream.ts"]
                }
            )
        )
    )
    assert check.analyse(json.loads(data_path.read_text()), src)["network_stale"] == [
        "services/api/stream.ts"
    ]
    assert check.main(["--update"], data_path, src) == 0
    assert json.loads(data_path.read_text())["network"]["modules"] == [
        "services/api/client.ts"
    ]
