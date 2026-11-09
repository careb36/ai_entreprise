import { api } from './api'

/** A terminal coding agent the company may name as a role's producer. */
export interface ConsoleCli {
  id: string
  label: string
  command: string
  cooldown_until: string | null
}

/** `GET /v1/companies/{id}/console_producers`: which CLI produces which role's tasks. */
export interface ConsoleProducers {
  company_id: number
  producers: Record<string, string>
  roles: string[]
  clis: ConsoleCli[]
  git_workflow: boolean
  /** The sandbox the CLI would run in: only `container` or `bwrap` with network on. */
  sandbox: { mode: string; network: boolean }
  /** Why no CLI runs while the setting stays saved (the model produces the tasks). */
  warnings: string[]
}

const url = (companyId: number) => `/v1/companies/${companyId}/console_producers`

export const consoleProducersApi = {
  get: (companyId: number) => api.get<ConsoleProducers>(url(companyId)),
  save: (companyId: number, producers: Record<string, string>) =>
    api.put<ConsoleProducers>(url(companyId), { producers }),
}
