import type { TFunction } from 'i18next'

/**
 * What a helper that renders text needs from i18next: the component's `t`, or a
 * plain function in a test. i18next 27 types `t` strictly (its options are
 * required when a string has variables), so a hand-written signature no longer
 * accepts it; this union does.
 */
export type Translate = TFunction | ((key: string, options?: Record<string, unknown>) => string)
