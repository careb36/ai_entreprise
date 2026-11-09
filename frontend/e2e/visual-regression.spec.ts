import { test, expect } from '@playwright/test';

test.describe('Visual Regression Tests - Laws of UX Implementation', () => {
  test.beforeEach(async ({ page }) => {
    // Disable CSS animations & transitions for deterministic visual snapshot testing
    await page.addInitScript(() => {
      const style = document.createElement('style');
      style.innerHTML = `
        *, *::before, *::after {
          animation-duration: 0s !important;
          animation-delay: 0s !important;
          transition-duration: 0s !important;
          transition-delay: 0s !important;
        }
      `;
      document.head.appendChild(style);
    });
  });

  test('AC-VISUAL-01: Navigation header & sub-navigation visual regression under Miller & Hick Laws', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    // Verify main navigation bar (Miller's Law <= 7 items)
    const navBar = page.locator('[data-testid="main-navigation"]');
    await expect(navBar).toBeVisible();
    await expect(navBar).toHaveScreenshot('navigation-header-desktop.png');

    // Mobile navigation viewport test
    await page.setViewportSize({ width: 375, height: 667 });
    await expect(navBar).toHaveScreenshot('navigation-header-mobile.png');
  });

  test('AC-VISUAL-02: Skeleton loaders visual state during async validation node execution', async ({ page }) => {
    // Mock API endpoint to stall response so skeleton loader remains active
    await page.route('/api/v1/validation/gates', async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 5000));
      await route.fulfill({ status: 200, json: [] });
    });

    await page.goto('/validation-gates');

    // Verify skeleton loader element is visible and matches baseline
    const skeletonLoader = page.locator('[data-testid="skeleton-dashboard-loader"]');
    await expect(skeletonLoader).toBeVisible();
    await expect(skeletonLoader).toHaveScreenshot('skeleton-loader-dashboard.png');
  });

  test('AC-VISUAL-03: Visual regression for Validation Gate Dashboard and Fitts Law primary actions', async ({ page }) => {
    await page.goto('/validation-gates');
    await page.waitForLoadState('networkidle');

    // Verify Validation Gate Dashboard loaded view
    const dashboard = page.locator('[data-testid="validation-gate-dashboard"]');
    await expect(dashboard).toBeVisible();
    await expect(dashboard).toHaveScreenshot('validation-gate-dashboard-full.png');

    // Verify Fitts's Law Primary Actions block (large hit targets, distinct styling)
    const primaryActions = page.locator('[data-testid="fitts-primary-actions"]');
    await expect(primaryActions).toBeVisible();
    await expect(primaryActions).toHaveScreenshot('fitts-primary-actions-toolbar.png');

    // Hover state visual check on primary action button
    const primaryButton = page.locator('[data-testid="fitts-primary-action-approve"]');
    if (await primaryButton.isVisible()) {
      await primaryButton.hover();
      await expect(primaryButton).toHaveScreenshot('fitts-primary-button-hover.png');
    }
  });
});
