// The backend's client, one module per domain under ./api/. This file keeps
// the single `api` object and every name callers already import.
import { createHttp } from './api/client'
import { agentEndpoints } from './api/agents'
import { analyticsEndpoints } from './api/analytics'
import { authEndpoints } from './api/auth'
import { chatEndpoints } from './api/chat'
import { boardNoteEndpoints } from './api/boardNotes'
import { companyEndpoints } from './api/companies'
import { evidenceEndpoints } from './api/evidence'
import { githubEndpoints } from './api/github'
import { invitationEndpoints } from './api/invitations'
import { memoryEndpoints } from './api/memory'
import { messageEndpoints } from './api/messages'
import { progressEndpoints } from './api/progress'
import { sprintEndpoints } from './api/sprints'
import { ssoEndpoints } from './api/sso'
import { supportEndpoints } from './api/support'
import { systemEndpoints } from './api/system'
import { dutiesEndpoints } from './api/duties'
import { taskViewEndpoints } from './api/taskViews'
import { taskRelationEndpoints } from './api/taskRelations'
import { teamEndpoints } from './api/teams'
import { usageEndpoints } from './api/usage'
import { wikiEndpoints } from './api/wiki'
import { workspaceEndpoints } from './api/workspace'

export { API_BASE, ApiError, errorDetail, fetchApi, fetchText, isClientError, setCsrfToken, setOnUnauthorized, setSessionHooks } from './api/client'
export type { SessionHooks } from './api/client'
export * from './api/agents'
export * from './api/analytics'
export * from './api/auth'
export * from './api/chat'
export * from './api/boardNotes'
export * from './api/companies'
export * from './api/evidence'
export * from './api/github'
export * from './api/invitations'
export * from './api/memory'
export * from './api/messages'
export * from './api/progress'
export * from './api/sprints'
export * from './api/sso'
export * from './api/stream'
export * from './api/support'
export * from './api/system'
export * from './api/duties'
export * from './api/taskViews'
export * from './api/taskRelations'
export * from './api/teams'
export * from './api/usage'
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
  ...invitationEndpoints(http),
  ...workspaceEndpoints(),
  ...messageEndpoints(http),
  ...boardNoteEndpoints(http),
  ...taskViewEndpoints(http),
  ...progressEndpoints(http),
  ...taskRelationEndpoints(http),
  ...evidenceEndpoints(http),
  ...analyticsEndpoints(http),
  ...memoryEndpoints(),
  ...supportEndpoints(http),
  ...githubEndpoints(http),
  ...ssoEndpoints(http),
  ...wikiEndpoints(http),
  ...chatEndpoints(http),
  ...teamEndpoints(http),
  ...usageEndpoints(http),
  ...dutiesEndpoints(http),
}

export const api = Object.assign(http, endpoints)
