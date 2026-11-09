# Doherty Threshold Implementation: Graph Skeleton Loaders

## Executive Summary
In accordance with the Laws of UX, specifically the **Doherty Threshold**, human-computer interaction productivity and engagement significantly increase when system response and perceived latency remain below **400ms**.

Previously, graph views (LangGraph execution flow traces and Neo4j provenance graphs) rendered generic static spinners or empty panels during asynchronous queries, resulting in high perceived latency, disorientation, and layout shift.

This deliverable implements dedicated SVG-based skeleton loaders (`GraphSkeleton`, `LineageGraphSkeleton`, `LangGraphTraceSkeleton`) that mount in under 50ms, visually mapping topological structures before data arrives.

## Architecture and Components

### 1. `GraphSkeleton.tsx`
- **Doherty Constant**: Enforces `DOHERTY_THRESHOLD_MS = 400` metric validation.
- **Topology Previews**:
  - `neo4j-lineage`: Multi-entity circular graph network with simulated relationship edges.
  - `langgraph-trace`: Agent orchestration node pipeline with conditional routing branches.
- **Micro-Animations**: Shimmer gradients and CSS pulse effects to assure users that computation is progressing smoothly.
- **Accessibility**: Includes `role="status"`, `aria-busy="true"`, and screen-reader announcements.

### 2. `LoadingSkeleton.tsx`
- Reusable primitives for cards, text rows, metrics, and side panels.

### 3. Integrated Views (`Lineage.tsx`)
- Fully integrated into `/lineage` (Neo4j and LangGraph provenance dashboard).
- Immediate skeleton rendering during loading state with zero cumulative layout shift (CLS).
- Automated tests verifying transitions within the sub-400ms window.
