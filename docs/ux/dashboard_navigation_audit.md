# UX Audit: Dashboard Navigation & Views

**Author:** Product Owner
**Product:** La Fabrique AI Code Verification & Evidence Layer
**Focus:** Miller's Law & Hick's Law Application

## 1. Executive Summary
As we scale our AI code verification platform, our React exploration interfaces must provide clear, actionable developer feedback and support rigorous compliance audits. Currently, our dashboard navigation has grown organically, leading to cognitive overload. This audit maps our current views against Miller's Law (working memory capacity is 7±2 items) and Hick's Law (decision time increases with the number of choices), proposing targeted changes to prioritize evidentiary integrity and streamline workflows.

## 2. Current State Analysis

### 2.1 Main Navigation (Violates Miller's Law)
Our current sidebar contains 13 top-level items:
1. Dashboard
2. Generated Artifacts
3. Verification Runs
4. LangGraph Pipelines
5. Evidence Records
6. Audit Logs
7. Agent Profiles
8. Compliance Policies
9. Sandbox Environments
10. Settings
11. API Keys
12. Webhooks
13. User Profile

*Finding:* 13 items exceed the 7±2 limit prescribed by Miller's Law. Developers and auditors are forced to scan a long, flat list, increasing cognitive friction when locating critical compliance records.

### 2.2 Verification Run View (Violates Hick's Law)
On a single Verification Run page, users are presented with a flat array of 9 action buttons (Approve, Reject, View AST Logs, Download Evidence Record, Re-run Pipeline, Edit Policy, Assign Auditor, Share, Delete).

*Finding:* Presenting 9 unweighted choices exponentially increases the time to make a decision (Hick's Law), delaying actionable developer feedback and increasing the risk of accidental misconfigurations.

## 3. Actionable Navigation Changes

To align with our product priorities—customer compliance needs, evidentiary integrity, and actionable developer feedback—I am defining the following 4 structural changes:

### Change 1: Consolidate Navigation into 5 Core Hubs (Miller's Law)
Group the 13 top-level items into 5 primary hubs to reduce cognitive load:
- **Overview:** Dashboard
- **Verification & Evidence:** Generated Artifacts, Verification Runs, Evidence Records, Audit Logs
- **Autonomous Agents:** LangGraph Pipelines, Agent Profiles, Sandbox Environments
- **Compliance:** Compliance Policies
- **Configuration:** Settings, API Keys, Webhooks, User Profile

### Change 2: Unify Evidence Records and Audit Logs
*Action:* Merge "Evidence Records" and "Audit Logs" into a single "Audit & Evidence" view with a tabbed interface.
*Rationale:* Auditors and compliance officers view these concepts as part of the same evidentiary lifecycle. Grouping them reduces navigation choices and reinforces our core value proposition of an audit-proof record.

### Change 3: Progressive Disclosure in Verification Runs (Hick's Law)
*Action:* Restructure the Verification Run action menu.
- **Primary Actions (Visible):** "Approve Artifact" and "Reject Artifact".
- **Secondary Actions (Dropdown Menu):** "View AST Logs", "Download Evidence Record", "Re-run Pipeline", "Assign Auditor", "Share".
- **Destructive Actions (Isolated):** "Delete" moved to a separate danger zone.
*Rationale:* By reducing immediate choices from 9 to 2, we drastically decrease decision time and focus the user on the primary verification workflow.

### Change 4: Contextual Settings Migration
*Action:* Remove "API Keys" and "Webhooks" from the global navigation entirely. Move them into a secondary navigation menu within the "Configuration" hub.
*Rationale:* These are low-frequency actions. Removing them from the global view simplifies the daily workflow for developers and auditors.

## 4. Quality Attributes & Targets (STD-PO-1)

To ensure these UX improvements meet our rigorous factory standards, the following quality attributes must be tested and verified by QA:

- **Usability (Navigation Efficiency):** Users must be able to locate and load the "Evidence Records" view from the home dashboard in `< 2.5 seconds` on average.
- **Usability (Decision Time):** The time taken to select a primary action (Approve/Reject) on the Verification Run view must average `< 3.0 seconds`.
- **Accessibility (Click Targets):** `100%` of the new navigation hub items and dropdown actions must have a minimum click target size of `44x44` CSS pixels to meet WCAG 2.1 AA standards.
- **Performance (Render Latency):** The refactored React navigation component must mount and render on the client side in `< 50 milliseconds`.
- **Security (Access Control):** `100%` of unauthorized attempts to access the "Configuration" hub via direct URL routing must result in a `403 Forbidden` redirect in `< 100 milliseconds`.
