import { useQuery } from '@tanstack/react-query'
import { api } from '../services/api'

const findingsKey = (companyId: number | null) => ['loop-findings', companyId] as const
const findingKey = (findingId: number | null) => ['loop-finding', findingId] as const

/** The company's self-improvement findings, newest first (any member reads them). */
export function useFindings(companyId: number | null) {
  return useQuery({
    queryKey: findingsKey(companyId),
    queryFn: () => api.getFindings(companyId as number),
    enabled: companyId !== null,
  })
}

/** One finding, with its specification text and every cycle's outcome. */
export function useFinding(findingId: number | null) {
  return useQuery({
    queryKey: findingKey(findingId),
    queryFn: () => api.getFinding(findingId as number),
    enabled: findingId !== null,
  })
}
