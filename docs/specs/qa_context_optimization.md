# Technical Specification: QA Context Payload Optimization

## Overview
Automated verification runs by the QA Agent require supplying source code, test requirements, and relevant schema definitions. Previously, transmitting the full workspace snapshot generated heavy token payloads (often 40,000+ tokens per verification run), leading to LLM 502/504 gateway timeouts and verification flakiness.

The `QAContextOptimizer` addresses this bottleneck by filtering workspace inputs down strictly to:
1. Changed files under verification.
2. Directly referenced schemas (extracted via static dependency analysis of imports/type references).
3. Exact acceptance criteria / test requirement specifications.

## Architecture & Data Flow

