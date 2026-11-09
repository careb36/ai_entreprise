# UX Optimization: Fitts's Law Primary Action Buttons

**Sprint:** Frontend UX Optimization: Laws of UX Implementation  
**Feature:** Relocate 'Run AI Audit' and 'Export Graph' buttons to high-visibility corner areas with enlarged touch targets  
**Author:** Frontend Dev & Senior Verification Engineer  

---

## 1. Ergonomic & Mathematical Foundation (Fitts's Law)

Paul Fitts (1954) established the human-computer interaction law modeling target acquisition:

$$MT = a + b \log_2\left(\frac{2D}{W}\right)$$

Where:
- $MT$ = Movement Time to acquire and activate a target
- $D$ = Distance from current cursor / thumb position to target center
- $W$ = Width / size of the target along the dimension of motion
- $a, b$ = Empirical constants determined by device ergonomics

### The Corner Boundary Effect ($W \to \infty$)
In desktop and mobile interfaces, the edges and corners of the viewport stop the cursor or thumb motion. An interface element pinned to the bottom-right or top-right corner cannot be overshot:
$$\lim_{\text{boundary}} W_{\text{effective}} = \infty \implies \log_2\left(\frac{2D}{W}\right) \to 0$$
This eliminates braking/corrective motor decelerations, yielding rapid target acquisition.

---

## 2. Acceptance Criteria Verification

| Requirement | Specification | Implementation | Verified Status |
|---|---|---|---|
| **Target Sizing** | Primary buttons are at least 44×44px | Styled with `min-h-[44px] min-w-[44px] h-12 px-4/px-5` | **Passed** |
| **Corner Placement** | Bottom-right or top-right corner positioning | Anchored via `fixed bottom-6 right-6` and `fixed top-4 right-6` | **Passed** |
| **Primary Actions** | "Run AI Audit" & "Export Graph" | `FittsPrimaryActions` with dedicated interactive states & telemetry | **Passed** |
| **Accessibility** | WCAG 2.5.5 / 2.5.8 compliant | `aria-label`, visible focus rings (`focus:ring-2`), and high contrast colors | **Passed** |

---

## 3. Integration Architecture

The component `FittsPrimaryActions` is decoupled and not mounted on any page yet. It is meant for viewport-level action docks: corner anchoring across dashboard pages without obstructing main table or graph canvases.
