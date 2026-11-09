# Automated Visual Regression Testing Suite

## Overview
This document specifies the visual regression testing infrastructure implemented for La Fabrique's React frontend UX updates (Miller's Law navigation, skeleton loaders, and Fitts's Law action targets).

## Architecture & Tooling
- **Engine**: Playwright Test (`@playwright/test`)
- **Assertion Mode**: Pixel-level comparison via `toHaveScreenshot()`
- **Thresholds**:
  - `maxDiffPixelRatio`: 0.01 (1% max pixel variance allowed)
  - `threshold`: 0.2 (per-pixel color difference sensitivity)
- **CI Enforcement**: GitHub Actions (`.github/workflows/visual-regression.yml`) blocks pull requests when visual regressions are detected.

## Covered Acceptance Criteria
- **AC-VISUAL-01**: Playwright snapshot configuration & webServer integration.
- **AC-VISUAL-02**: Visual integrity testing for Navigation header (Desktop & Mobile) and Skeleton Loaders during async execution.
- **AC-VISUAL-03**: Visual integrity testing for Validation Gate Dashboard and Fitts's Law primary actions, backed by automated Pytest validation (`tests/test_visual_regression_ci.py`).

## Running Tests Locally
```bash
# Run visual regression suite
cd frontend
npm run test:visual

# Update snapshot baselines after approved UX changes
npm run test:visual:update
