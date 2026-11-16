// The React Query hooks over the backend's client, one module per domain under
// hooks/queries/. Callers (and the tests that mock this module) import them
// from here.

// The keys live in services (the service hooks invalidate them too); they are
// re-exported here for the callers that import them from the hooks.
export { QUERY_KEYS } from '../services/queryKeys'
export * from './queries/system'
export * from './queries/evidence'
export * from './queries/company'
export * from './queries/sprints'
export * from './queries/quotas'
export * from './queries/agents'
export * from './queries/helpDesk'
