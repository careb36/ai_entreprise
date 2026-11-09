# Automated Visual Regression Testing Architecture

## Overview
This document specifies the visual regression testing pipeline established for La Fabrique's React frontend. The suite protects against unintended visual layout shifts, font regressions, and broken component states introduced during Laws of UX updates (Miller's Law navigation simplification, Hick's Law option filtering, Fitts's Law action buttons, and Skeleton loading states).

## Architecture & Tooling
- **Engine**: Playwright Test (`@playwright/test`)
- **Assertion**: `expect(locator).toHaveScreenshot(snapshotName, options)`
- **Snapshot Parameters**:
  - `maxDiffPixelRatio`: `0.01` (1% max allowable pixel variance)
  - `threshold`: `0.2` (Per-pixel color sensitivity threshold)
  - `animations`: `'disabled'` (Enforces deterministic screenshot capture by suppressing CSS & SVG animations)
- **CI Pipeline Integration**: `.github/workflows/visual-regression.yml`

## Acceptance Criteria Coverage

| ID | Focus Area | Test Spec Location | Snapshot Baselines |
|---|---|---|---|
| `AC-VISUAL-01` | Primary Navigation & Miller's Law (≤ 7 Items) | `frontend/e2e/visual-regression.spec.ts` | `main-navigation-desktop.png`, `main-navigation-mobile-drawer.png` |
| `AC-VISUAL-02` | Skeleton Loader Component & Layout Stability | `frontend/e2e/visual-regression.spec.ts` | `skeleton-loaders-dashboard.png`, `skeleton-card-item.png` |
| `AC-VISUAL-03` | Validation Gate Dashboard & Fitts's Law Actions | `frontend/e2e/visual-regression.spec.ts` | `validation-gate-dashboard-full.png`, `fitts-primary-actions-toolbar.png`, `fitts-primary-button-hover.png` |

## Local Execution & Snapshot Management

### 1. Running Visual Regression Tests Locally
```bash
cd frontend
npm run build
npx playwright test
