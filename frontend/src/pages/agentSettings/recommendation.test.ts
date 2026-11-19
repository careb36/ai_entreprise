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

const PRO = ['Architect', 'PO', 'SecOps', 'Security', 'Reviewer']
const FLASH = ['ScrumMaster', 'Developer', 'QA', 'TechWriter', 'BackendDeveloper', 'FrontendDeveloper', 'DevOps', 'Designer', 'DataEngineer', 'Support', 'Unknown']
const SENIORITIES = ['Junior', 'Mid', 'Senior', 'Staff']

describe('recommendModelForRole', () => {
  it.each(PRO.flatMap(role => SENIORITIES.map(level => [role, level])))('recommends DeepSeek Pro for %s (%s)', (role, level) => {
    const rec = recommendModelForRole(catalogue, role, level)
    expect(rec.primary?.id).toBe(DEEPSEEK_PRO)
    expect(rec.alternatives.map(m => m.id)).toEqual([DEEPSEEK_FLASH])
    expect(rec.primaryInCatalogue).toBe(true)
  })

  it.each(FLASH.flatMap(role => SENIORITIES.map(level => [role, level])))('recommends DeepSeek Flash for %s (%s)', (role, level) => {
    const rec = recommendModelForRole(catalogue, role, level)
    expect(rec.primary?.id).toBe(DEEPSEEK_FLASH)
    expect(rec.alternatives.map(m => m.id)).toEqual([DEEPSEEK_PRO])
  })

  it('never recommends another provider', () => {
    for (const role of [...PRO, ...FLASH]) {
      const rec = recommendModelForRole(catalogue, role)
      for (const model of [rec.primary, ...rec.alternatives]) {
        expect(model?.id.startsWith('deepseek-')).toBe(true)
      }
    }
  })

  it('still recommends DeepSeek when the live catalogue does not list it', () => {
    const geminiOnly = catalogue.filter(m => m.provider === 'gemini')
    const rec = recommendModelForRole(geminiOnly, 'Architect')
    expect(rec.primary?.id).toBe(DEEPSEEK_PRO)
    expect(rec.primary?.provider).toBe('deepseek')
    expect(rec.alternatives.map(m => m.id)).toEqual([DEEPSEEK_FLASH])
    expect(rec.primaryInCatalogue).toBe(false)
    expect(recommendModelForRole([], 'Developer').primary?.id).toBe(DEEPSEEK_FLASH)
  })

  it('replaces earlier automatic picks, the retired DeepSeek ids included', () => {
    for (const id of [DEEPSEEK_PRO, DEEPSEEK_FLASH, 'deepseek-chat', 'deepseek-reasoner', 'deepseek-coder', 'gemini-3.8-flash']) {
      expect(AUTO_RECOMMENDED_MODEL_IDS.has(id)).toBe(true)
    }
  })
})
