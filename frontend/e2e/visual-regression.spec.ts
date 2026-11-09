import { test, expect } from '@playwright/test';

/**
 * Acceptance Test Suite for Visual Regression Testing on Laws of UX Updates.
 * Covers:
 * - AC-VISUAL-01: Navigation visual integrity (Miller's Law <= 7 nav items, responsive drawer)
 * - AC-VISUAL-02: Skeleton Loaders visual integrity (Loading states layout stability)
 * - AC-VISUAL-03: Fitts's Law Primary Actions and Validation Gate Dashboard layout
 */

test.describe('UX Visual Regression Suite', () => {
  test.beforeEach(async ({ page }) => {
    // Inject deterministic CSS flags to disable animations during snapshot capture
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

  test('AC-VISUAL-01: Visual regression for primary navigation and header layout', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    // Verify navigation bar visual structure
    const navBar = page.locator('[data-testid="main-navigation"]');
    await expect(navBar).toBeVisible();

    // Verify Miller's Law constraint: Navigation menu must not exceed 7 primary items
    const navItems = page.locator('[data-testid="main-navigation"] nav a, [data-testid="main-navigation"] nav button');
    const itemCount = await navItems.count();
    expect(itemCount).toBeLessThanOrEqual(7);

    // Capture visual baseline for main navigation header
    await expect(navBar).toHaveScreenshot('main-navigation-desktop.png');

    // Test mobile layout responsive view if viewport is mobile width
    const viewport = page.viewportSize();
    if (viewport && viewport.width <= 480) {
      const mobileToggle = page.locator('[data-testid="mobile-menu-toggle"]');
      if (await mobileToggle.isVisible()) {
        await mobileToggle.click();
        const drawer = page.locator('[data-testid="mobile-navigation-drawer"]');
        await expect(drawer).toBeVisible();
        await expect(drawer).toHaveScreenshot('main-navigation-mobile-drawer.png');
      }
    }
  });

  test('AC-VISUAL-02: Visual regression for skeleton loader loading states', async ({ page }) => {
    // Force loading state by hitting route with delayed data mock or skeleton query parameter
    await page.goto('/?state=loading');

    // Wait for skeleton loaders to be rendered
    const skeletonContainer = page.locator('[data-testid="skeleton-dashboard-loader"]');
    await expect(skeletonContainer).toBeVisible({ timeout: 5000 });

    // Validate visual screenshot of skeleton state
    await expect(skeletonContainer).toHaveScreenshot('skeleton-loaders-dashboard.png');

    // Validate individual skeleton card pulse layout bounds
    const skeletonCards = page.locator('[data-testid="skeleton-card"]');
    if (await skeletonCards.count() > 0) {
      await expect(skeletonCards.first()).toHaveScreenshot('skeleton-card-item.png');
    }
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
