import type { Http } from './client'

export type ChatProvider = 'slack' | 'teams'

/** A company's Slack app or Teams webhooks, as the backend shows it: never a credential. */
export interface ChatConnection {
  id: number
  company_id: number
  provider: ChatProvider
  workspace_id: string
  channel: string | null
  enabled: boolean
  has_post_credential: boolean
  has_signing_secret: boolean
  inbound_url: string
  tested_at: string | null
  test_ok: boolean | null
  test_detail: string | null
  last_status: string | null
  last_error: string | null
  updated_at: string | null
}

export interface ChatSettings {
  company_id: number
  providers: ChatProvider[]
  connections: Record<ChatProvider, ChatConnection | null>
}

export interface ChatConnectionInput {
  workspace_id: string
  channel?: string | null
  /** Omitted keeps the stored credential. */
  post_credential?: string
  signing_secret?: string
  enabled: boolean
}

export interface ChatTestResult {
  ok: boolean
  detail: string
  observed: Record<string, unknown>
  connection: ChatConnection
}

export interface ChatLink {
  provider: ChatProvider
  workspace_id: string
  external_user_id: string
  display_name: string | null
  created_at: string | null
}

export interface ChatLinks {
  company_id: number
  connected: Record<ChatProvider, boolean>
  links: ChatLink[]
}

export interface ChatLinkCode {
  code: string
  expires_at: string
  provider: ChatProvider
  instruction: string
}

export const chatEndpoints = (http: Http) => ({
  getChat: (companyId: number) => http.get<ChatSettings>(`/v1/companies/${companyId}/chat`),

  saveChat: (companyId: number, provider: ChatProvider, data: ChatConnectionInput) =>
    http.put<ChatConnection>(`/v1/companies/${companyId}/chat/${provider}`, data),

  testChat: (companyId: number, provider: ChatProvider) =>
    http.post<ChatTestResult>(`/v1/companies/${companyId}/chat/${provider}/test`),

  deleteChat: (companyId: number, provider: ChatProvider) =>
    http.delete<void>(`/v1/companies/${companyId}/chat/${provider}`),

  getChatLinks: (companyId: number) => http.get<ChatLinks>(`/v1/companies/${companyId}/chat/links`),

  chatLinkCode: (companyId: number, provider: ChatProvider) =>
    http.post<ChatLinkCode>(`/v1/companies/${companyId}/chat/links/${provider}/code`),

  unlinkChat: (companyId: number, provider: ChatProvider) =>
    http.delete<void>(`/v1/companies/${companyId}/chat/links/${provider}`),
})
