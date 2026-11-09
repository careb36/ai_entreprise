# La Fabrique Performance & Usability Benchmark Report

**Author:** QA Engineering  
**Sprint:** Frontend UX Optimization: Laws of UX Implementation  
**Target SLA:** Doherty Threshold (<400ms TTI & LCP)  
**Status:** PASSED  

---

## 1. Overview & Objectives

The Doherty Threshold asserts that computer-human interaction productivity increases exponentially when system response times are kept under **400ms**. In La Fabrique's AI artifact verification system, high interactive latency during code validation, graph navigation, or trigger execution breaks human focus and degrades audit confidence.

This benchmark measures **Time-to-Interactive (TTI)** and **Largest Contentful Paint (LCP)** before and after the Sprint 3 UX and Caching optimizations across three core AI dashboard workflows:

1. **Workflow A:** AI Validation Gate Execution & Visualization Loop
2. **Workflow B:** Dependency Graph Rendering & Sub-400ms Cache Hydration
3. **Workflow C:** Fitts's Law Primary Action Trigger & State Transition

---

## 2. Methodology & Test Setup

- **Sample Size:** 1,000 automated execution iterations per workflow under simulated network conditions (50ms RTT latency, 100Mbps bandwidth).
- **Instrumentation:** Automated performance profiler using standard Navigation Timing API, Resource Timing API, and backend high-resolution counters (`time.perf_counter_ns`).
- **Target Metrics:**
  - **TTI (Time-to-Interactive):** Time elapsed until the primary interaction buttons and graph nodes are completely rendered and responsive to user inputs.
  - **LCP (Largest Contentful Paint):** Time elapsed until the main visual content element (AST graph visualizer / validation verdict card) is fully rendered.
  - **Doherty Compliance SLA:** Latency P95 < 400ms, P99 < 400ms.

---

## 3. Before vs. After Benchmark Results

### 3.1 Aggregate Summary Table

| Workflow ID | Workflow Name | Metric | Baseline (Pre-UX / Uncached) | Optimized (Post-UX / Cached) | Improvement (%) | Doherty SLA (<400ms) |
|---|---|---|---|---|---|---|
| **WF-A** | AI Validation Gate | **TTI (P95)** | 850.0 ms | **142.0 ms** | 83.3% | **PASSED** |
| **WF-A** | AI Validation Gate | **LCP (P95)** | 620.0 ms | **185.0 ms** | 70.2% | **PASSED** |
| **WF-B** | Graph Cache Hydration | **TTI (P95)** | 920.0 ms | **110.0 ms** | 88.0% | **PASSED** |
| **WF-B** | Graph Cache Hydration | **LCP (P95)** | 780.0 ms | **135.0 ms** | 82.7% | **PASSED** |
| **WF-C** | Fitts's Primary Actions | **TTI (P95)** | 480.0 ms | **68.0 ms** | 85.8% | **PASSED** |
| **WF-C** | Fitts's Primary Actions | **LCP (P95)** | 390.0 ms | **95.0 ms** | 75.6% | **PASSED** |

---

## 4. Latency Distribution & Percentiles (Post-Optimization)

