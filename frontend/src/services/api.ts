// The backend's client, one module per domain under ./api/. This file keeps
// the single `api` object and every name callers already import.
import { createHttp } from './api/client'
import { agentEndpoints } from './api/agents'
import { analyticsEndpoints } from './api/analytics'
import { authEndpoints } from './api/auth'
import { boardNoteEndpoints } from './api/boardNotes'
import { companyEndpoints } from './api/companies'
import { evidenceEndpoints } from './api/evidence'
import { githubEndpoints } from './api/github'
import { memoryEndpoints } from './api/memory'
import { messageEndpoints } from './api/messages'
import { sprintEndpoints } from './api/sprints'
import { ssoEndpoints } from './api/sso'
import { supportEndpoints } from './api/support'
import { systemEndpoints } from './api/system'
import { wikiEndpoints } from './api/wiki'
import { workspaceEndpoints } from './api/workspace'

export { API_BASE, ApiError, errorDetail, fetchApi, fetchText, isClientError, setOnUnauthorized } from './api/client'
export * from './api/agents'
export * from './api/analytics'
export * from './api/auth'
export * from './api/boardNotes'
export * from './api/companies'
export * from './api/evidence'
export * from './api/github'
export * from './api/memory'
export * from './api/messages'
export * from './api/sprints'
export * from './api/sso'
export * from './api/stream'
export * from './api/support'
export * from './api/system'
export * from './api/wiki'
export * from './api/workspace'

// ── Public API client ─────────────────────────────────────────────────────────

// One object: each domain's endpoints call the verbs through it, so replacing
// `api.get` (as a test spy does) reaches every endpoint built on it.
const http = createHttp()

const endpoints = {
  ...agentEndpoints(http),
  ...sprintEndpoints(http),
  ...authEndpoints(),
  ...systemEndpoints(http),
  ...companyEndpoints(http),
  ...workspaceEndpoints(),
  ...messageEndpoints(http),
  ...boardNoteEndpoints(http),
  ...evidenceEndpoints(http),
  ...analyticsEndpoints(http),
  ...memoryEndpoints(),
  ...supportEndpoints(http),
  ...githubEndpoints(http),
  ...ssoEndpoints(http),
  ...wikiEndpoints(http),
}

export const api = Object.assign(http, endpoints)
