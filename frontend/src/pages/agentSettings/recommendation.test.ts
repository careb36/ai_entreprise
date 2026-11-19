import { describe, expect, it } from 'vitest'
import type { ModelCatalogEntry } from '../../services/api'
import { AUTO_RECOMMENDED_MODEL_IDS, DEEPSEEK_FLASH, DEEPSEEK_PRO, recommendModelForRole } from './recommendation'

const entry = (id: string, provider: string): ModelCatalogEntry =>
  ({ id, provider, tier: 'balanced', description: id })

const catalogue = [
  entry('deepseek-flash', 'deepseek'),
  entry('deepseek-v4-pro', 'deepseek'),
  entry('gemini-3.8-flash', 'gemini'),
  entry('gemini-3.1-pro-preview', 'gemini'),
]

describe('recommendModelForRole', () => {
  it.each(['Architect', 'PO', 'SecOps', 'Security', 'Reviewer'])('leads with DeepSeek Pro for %s', role => {
    expect(recommendModelForRole(catalogue, role).primary?.id).toBe(DEEPSEEK_PRO)
  })

  it.each(['ScrumMaster', 'Developer', 'QA', 'TechWriter', 'BackendDeveloper', 'FrontendDeveloper', 'DevOps', 'Designer', 'DataEngineer', 'Support'])(
    'leads with DeepSeek Flash for %s',
    role => {
      expect(recommendModelForRole(catalogue, role).primary?.id).toBe(DEEPSEEK_FLASH)
    },
  )

  it('keeps the other providers as alternatives', () => {
    const rec = recommendModelForRole(catalogue, 'Architect')
    expect(rec.alternatives.map(m => m.id)).toContain('gemini-3.1-pro-preview')
  })

  it('falls back to the role list when the catalogue has no DeepSeek model', () => {
    const geminiOnly = catalogue.filter(m => m.provider === 'gemini')
    expect(recommendModelForRole(geminiOnly, 'Architect').primary?.id).toBe('gemini-3.1-pro-preview')
  })

  it('replaces an auto-recommended DeepSeek model when the role changes', () => {
    expect(AUTO_RECOMMENDED_MODEL_IDS.has(DEEPSEEK_PRO)).toBe(true)
    expect(AUTO_RECOMMENDED_MODEL_IDS.has(DEEPSEEK_FLASH)).toBe(true)
  })
})
