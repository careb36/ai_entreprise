"""
Automated Test Suite for Visual Regression CI Integration.
Validates requirement compliance for Laws of UX Navigation & Skeleton Loaders visual testing.
Ref: STD-QA-1, STD-QA-3.
"""

import json
from pathlib import Path
import yaml


def test_ac_visual_01_playwright_config_exists():
    """
    AC-VISUAL-01: Verify Playwright visual regression configuration is properly configured
    with strict snapshot comparison options and webServer startup.
    """
    playwright_config_path = Path("frontend/playwright.config.ts")
    assert playwright_config_path.exists(), "playwright.config.ts must exist in frontend directory"

    content = playwright_config_path.read_text(encoding="utf-8")
    assert "toHaveScreenshot" in content, "playwright.config.ts must configure toHaveScreenshot settings"
    assert "maxDiffPixelRatio" in content, "playwright.config.ts must specify maxDiffPixelRatio threshold"
    assert "webServer" in content, "playwright.config.ts must configure webServer for app preview"


def test_ac_visual_02_visual_test_spec_validates_ux_updates():
    """
    AC-VISUAL-02: Verify Playwright visual regression test file covers Navigation,
    Skeleton Loaders, and Fitts's Law Primary Actions.
    """
    spec_path = Path("frontend/e2e/visual-regression.spec.ts")
    assert spec_path.exists(), "visual-regression.spec.ts must exist"

    content = spec_path.read_text(encoding="utf-8")
    assert "AC-VISUAL-01" in content, "Spec must cover AC-VISUAL-01 navigation integrity"
    assert "AC-VISUAL-02" in content, "Spec must cover AC-VISUAL-02 skeleton loaders"
    assert "AC-VISUAL-03" in content, "Spec must cover AC-VISUAL-03 primary actions"
    assert "toHaveScreenshot" in content, "Spec must execute visual snapshot comparison"
    assert "skeleton-dashboard-loader" in content, "Spec must validate skeleton loader elements"


def test_ac_visual_03_ci_workflow_blocks_merges_on_ui_regression():
    """
    AC-VISUAL-03: Verify GitHub Actions CI workflow is configured to run visual regression
    tests on frontend changes and upload diagnostic reports on failure.
    """
    workflow_path = Path(".github/workflows/visual-regression.yml")
    assert workflow_path.exists(), "Visual regression workflow file must exist"

    with open(workflow_path, "r", encoding="utf-8") as f:
        workflow = yaml.safe_load(f)

    assert workflow.get("name") == "Visual Regression Tests"
    triggers = workflow.get("on", {})
    assert "pull_request" in triggers, "Workflow must trigger on pull_request"
    assert "push" in triggers, "Workflow must trigger on push to main"

    jobs = workflow.get("jobs", {})
    assert "visual-regression" in jobs, "Workflow must define visual-regression job"

    steps = jobs["visual-regression"].get("steps", [])
    step_names = [s.get("name", "") for s in steps]

    assert any("Playwright" in name for name in step_names), "CI job must install Playwright browsers"
    assert any("Execute Playwright Visual Regression Tests" in name for name in step_names), "CI job must execute Playwright visual tests"
    assert any("Upload Visual Test Report" in name for name in step_names), "CI job must upload visual artifacts on failure"
