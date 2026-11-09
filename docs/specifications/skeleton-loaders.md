# Standardized Skeleton Loader Architecture & Specification

## Objective
Provide a unified, highly-performant, accessible skeleton loading component library for the React/Electron client (`frontend/src/components/skeleton`). This directly mitigates past sprint issues where LLM agents struggled with custom loading states and graph data loading delays.

## Core Principles
1. **Doherty Threshold Compliance**: Provide instant perceptual visual feedback (<400ms) upon any asynchronous network invocation.
2. **Accessibility (WCAG 2.1 AA)**: All skeletons carry `role="status"`, `aria-busy="true"`, and `aria-live="polite"` with invisible screen-reader text (`sr-only`).
3. **Composable Design**: Low-level primitives (`SkeletonBase`) easily compose into higher-order patterns (`SkeletonText`, `SkeletonCard`, `SkeletonTable`, `SkeletonGraph`).
4. **Theme Resilience**: Uses dark/light slate tokens and CSS animators compatible with Tailwind CSS.

## Usage Guide

### 1. Primitive Loader
```tsx
import { SkeletonBase } from '@/components/skeleton';

<SkeletonBase variant="circular" width="40px" height="40px" />
<SkeletonBase variant="rectangular" width="100%" height="120px" animation="wave" />
