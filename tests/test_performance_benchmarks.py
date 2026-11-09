"""
Automated Pytest Suite validating performance benchmarking and Doherty Threshold compliance.
Adheres strictly to STD-QA-1 (One acceptance test per criterion, written first).
"""

import pytest
from src.la_fabrique.performance_benchmarks import (
    WorkflowMetric,
    PerformanceReport,
    evaluate_default_dashboard_workflows,
    DOHERTY_THRESHOLD_MS,
)


def test_ac_performance_report_generated():
    """
    Criterion Test (STD-QA-1 ID: test_ac_performance_report_generated):
    Validates that a performance report is generated showing before/after metrics
    for the core AI dashboard workflows, and that all metrics strictly satisfy the
    Doherty Threshold (<400ms for TTI and LCP).
    """
    report = evaluate_default_dashboard_workflows()

    assert report is not None
    assert len(report.workflows) == 3, "Expected 3 core AI dashboard workflows in performance report"

    # Verify each core workflow has before/after metrics and passes Doherty Threshold
    for wf_id, metric in report.workflows.items():
        assert metric.baseline_tti_ms > 0.0, f"Baseline TTI must be positive for {wf_id}"
        assert metric.baseline_lcp_ms > 0.0, f"Baseline LCP must be positive for {wf_id}"
        assert metric.tti_p95 < DOHERTY_THRESHOLD_MS, (
            f"Workflow {wf_id} TTI P95 ({metric.tti_p95}ms) exceeds Doherty Threshold ({DOHERTY_THRESHOLD_MS}ms)"
        )
        assert metric.lcp_p95 < DOHERTY_THRESHOLD_MS, (
            f"Workflow {wf_id} LCP P95 ({metric.lcp_p95}ms) exceeds Doherty Threshold ({DOHERTY_THRESHOLD_MS}ms)"
        )
        assert metric.tti_improvement_pct > 0.0, f"TTI improvement percentage must be positive for {wf_id}"
        assert metric.lcp_improvement_pct > 0.0, f"LCP improvement percentage must be positive for {wf_id}"
        assert metric.passes_doherty_threshold is True

    assert report.all_pass_doherty() is True

    # Verify markdown summary generation
    markdown_output = report.to_markdown()
    assert "Performance Benchmark Summary" in markdown_output
    assert "Doherty Threshold SLA:" in markdown_output
    assert "PASSED" in markdown_output


def test_doherty_threshold_failure_detection():
    """
    Verifies that the performance engine correctly detects and flags workflows
    exceeding the 400ms Doherty Threshold limit.
    """
    slow_metric = WorkflowMetric(
        workflow_id="WF-SLOW",
        workflow_name="Slow Unoptimized Workflow",
        baseline_tti_ms=1200.0,
        baseline_lcp_ms=900.0,
        optimized_tti_ms=450.0,  # Exceeds 400ms threshold
        optimized_lcp_ms=380.0,
        samples_tti=[410.0, 430.0, 450.0, 480.0],
        samples_lcp=[350.0, 360.0, 380.0, 390.0],
    )

    report = PerformanceReport()
    report.add_workflow(slow_metric)

    assert slow_metric.passes_doherty_threshold is False
    assert report.all_pass_doherty() is False


def test_percentile_calculation_accuracy():
    """
    Validates mathematical accuracy of percentile calculations in benchmark metrics.
    """
    samples = [100.0, 200.0, 300.0, 400.0, 500.0]
    metric = WorkflowMetric(
        workflow_id="WF-CALC",
        workflow_name="Calculation Test",
        baseline_tti_ms=600.0,
        baseline_lcp_ms=600.0,
        optimized_tti_ms=300.0,
        optimized_lcp_ms=300.0,
        samples_tti=samples,
        samples_lcp=samples,
    )

    p95_tti = metric.tti_p95
    assert p95_tti == pytest.approx(480.0)
