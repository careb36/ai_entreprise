/**
 * Two things the architecture's backend offers beside its pages
 * (`routers/company_architecture.py`, `routers/architectures.py`): the next
 * draft version of the company's architecture for an approved specification
 * (a Manager; 409 while a revision is open, with the open draft's id), and an
 * architecture's decisions as ADR files (`docs/adr/NNNN-<slug>.md`, Viewer).
 */
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from './api'
import { architecturesKey, companyArchitectureKey, type Architecture } from './architectures'

export interface AdrFile {
  id: string
  number: number
  path: string
  markdown: string
}

/** A refused revision; `architectureId` names the open draft when that is why. */
export class RevisionRefused extends ApiError {
  constructor(status: number, message: string, public architectureId: number | null) {
    super(status, message, message)
    this.name = 'RevisionRefused'
  }
}

async function revise(companyId: number, specificationId: number): Promise<Architecture> {
  try {
    return await api.post<Architecture>(`/v1/companies/${companyId}/architecture/revisions`, { specification_id: specificationId })
  } catch (error) {
    if (error instanceof ApiError && error.data && typeof error.data === 'object') {
      const data = error.data as { architecture_id?: unknown }
      throw new RevisionRefused(error.status, errorMessage(error), typeof data.architecture_id === 'number' ? data.architecture_id : null)
    }
    throw error
  }
}

const errorMessage = (error: ApiError) => error.detail || error.message

export const architectureFilesApi = {
  revise,
  adrs: (architectureId: number, firstNumber = 1) =>
    api.get<{ architecture_id: number; files: AdrFile[] }>(`/v1/architectures/${architectureId}/adrs`, { first_number: firstNumber }),
}

export function useReviseArchitecture(companyId: number) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (specificationId: number) => revise(companyId, specificationId),
    onSuccess: architecture => {
      client.invalidateQueries({ queryKey: architecturesKey(architecture.specification_id) })
      client.invalidateQueries({ queryKey: companyArchitectureKey(companyId) })
    },
    // A revision opened meanwhile (409 with its id): the versions read again show it.
    onError: error => {
      if (error instanceof RevisionRefused && error.architectureId !== null) {
        client.invalidateQueries({ queryKey: companyArchitectureKey(companyId) })
      }
    },
  })
}

export function useAdrFiles(architectureId: number) {
  return useMutation({ mutationFn: (firstNumber: number) => architectureFilesApi.adrs(architectureId, firstNumber) })
}
