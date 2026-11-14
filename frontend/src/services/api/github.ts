import { fetchApi, type Http } from './client'

export type GitHubInclusionRule = 'all' | 'bots_only' | 'non_bots'

export interface GitHubBindingRequest {
  company_id: number
  owner: string
  repo: string
  webhook_secret: string
  inclusion_rule: GitHubInclusionRule
}

export interface GitHubBinding {
  id: number
  company_id: number
  owner: string
  repo: string
  inclusion_rule: GitHubInclusionRule
}

export interface GitHubBackfillResult {
  ingested: number
  skipped: number
  errors: { number: number | null; error: string }[]
}

export const githubEndpoints = (api: Http) => ({
  // GitHub integration (Manager). There is no endpoint that lists bindings.
  createGitHubBinding: (data: GitHubBindingRequest) =>
    api.post<GitHubBinding>('/v1/integrations/github/bindings', data),

  backfillGitHub: (data: { company_id: number; owner: string; repo: string; since?: string }) =>
    fetchApi<GitHubBackfillResult>('/v1/integrations/github/backfill', {
      method: 'POST',
      body: JSON.stringify(data),
      retryServerErrors: false,
    }),
})
