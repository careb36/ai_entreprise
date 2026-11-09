# Global Navigation Redesign: Application of Hick's Law

## Problem Statement & Context
Prior iterations of the La Fabrique frontend navigation suffered from high visual clutter and cognitive decision fatigue. With more than 7 top-level navigation choices displayed simultaneously on the top app bar and drawer menus, users experienced notable latency in locating core modules.

Per **Hick's Law**, the time $T$ required to make a decision is a logarithmic function of the number $n$ of available options:
$$T = b \cdot \log_2(n + 1)$$

By reducing top-level choices from $>7$ disparate items to **exactly 5 cohesive functional groups**, decision time and cognitive load are minimized while preserving instant access to all deep views and tools.

---

## The 5 Top-Level Group Hierarchy

| Group ID | Label | Primary Route | Sub-Items (Secondary Choices) | Rationale |
|---|---|---|---|---|
| `evidence` | **Evidence & Graph** | `/evidence` | Evidence Records, Knowledge Graph Lineage | Unifies all cryptographic proofs, signed execution claims, and visual lineage into a single verification hub. |
| `governance` | **Governance** | `/governance` | Review Inbox, Security Policies | Groups human-in-the-loop signoffs, pending approvals, and policy rules. Carries real-time pending badges. |
| `executions` | **Executions** | `/runs` | Agent Runs, Execution Console | Consolidates agent run states, LangGraph execution graphs, and sandbox outputs. |
| `audit` | **Audit Trail** | `/audit` | Audit Records, System Telemetry | Houses the immutable non-repudiable audit ledger, health telemetry, and token usage metrics. |
| `workspace` | **Workspace** | `/agents` | Agent Fleet, Quotas & Limits, Switch Company | Collects multi-tenant organizational management, agent configuration, and compute limits. |

---

## Architectural & Accessibility Highlights
1. **WCAG 2.1 AA Compliant Interaction**:
   - Primary buttons navigate directly to the group's default view.
   - Dropdown trigger buttons provide ARIA attributes (`aria-expanded`, `aria-haspopup="menu"`, `aria-label`).
   - Full keyboard navigation support: `Escape` closes menus and returns focus to the trigger; `ArrowDown`/`ArrowUp` cycles through sub-items.
2. **Responsive Sidebar Drawer**:
   - On mobile/tablet viewports, the navigation transforms into a sliding drawer featuring 5 collapsible accordion groups with minimum touch targets $\ge 44\text{px}$.
3. **Notification Badge Preservation**:
   - Pending governance items remain highlighted on both the top-level trigger and within the sub-item menu, ensuring critical human review notices are never missed.
4. **Automated UI Test Coverage**:
   - Unit and integration tests in `frontend/src/components/navigation/__tests__/Navigation.test.tsx` verify the 5-group ceiling, badge rendering, dropdown interactions, keyboard handling, and responsive sidebar behavior.
