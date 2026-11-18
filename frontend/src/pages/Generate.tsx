import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAppStore } from '../store/useAppStore'
import { useGenerate } from '../hooks/useQueries'
import { Building2, Code2, Loader2, ArrowRight, CheckCircle2, Copy, Check } from 'lucide-react'
import { PageHeader, Panel } from '../components/ui'

interface GenerateResult {
  interaction_id: string
  sprint_id?: number
  package_url?: string
}

export default function Generate() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const companyId = useAppStore(state => state.companyId)
  const companyName = useAppStore(state => state.companyName)
  const [story, setStory] = useState('')
  const [constraints, setConstraints] = useState('')
  const [result, setResult] = useState<GenerateResult | null>(null)
  const [copied, setCopied] = useState(false)

  const generateMutation = useGenerate()

  const loading = generateMutation.isPending
  const error = (generateMutation.error as any)?.message || null

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!story.trim()) return

    generateMutation.mutate({
      company_id: companyId ?? 1,
      story: story.trim(),
      constraints: constraints.trim() || undefined
    }, {
      onSuccess: (res: GenerateResult) => {
        setResult(res)
        setStory('')
        setConstraints('')
      }
    })
  }

  async function copyId(text: string) {
    await navigator.clipboard.writeText(text).catch(() => {})
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="page-container max-w-3xl space-y-4">
      <PageHeader
        title={t('generate.title', 'Generate')}
        subtitle={t('generate.subtitle', 'Describe a feature and let the AI team build it')}
        actions={
          <span className="chip h-8 gap-1.5 px-2.5 text-xs">
            <Building2 size={12} aria-hidden="true" className="text-gray-400" />
            <span className="text-gray-400">{t('generate.companyId', 'Company')}:</span>
            <span className="font-medium text-gray-100">{companyName ?? t('generate.companyFallback', { id: companyId ?? 1 })}</span>
          </span>
        }
      />

      {/* Success banner */}
      {result && (
        <div role="status" className="panel border-emerald-700/50 animate-slide-up motion-reduce:animate-none">
          <div className="panel-header">
            <CheckCircle2 size={14} aria-hidden="true" className="text-emerald-300" />
            <span className="panel-title">{t('generate.started', 'Generation started!')}</span>
          </div>

          <dl className="data-list text-xs">
            <div className="data-row flex flex-wrap items-center gap-2">
              <dt className="w-32 text-gray-400">{t('generate.interactionId', 'Interaction ID')}</dt>
              <dd className="flex min-w-0 flex-1 items-center gap-2">
                <code className="min-w-0 flex-1 truncate rounded-md bg-surface-800 px-2 py-0.5 font-mono text-brand-300">
                  {result.interaction_id}
                </code>
                <button
                  type="button"
                  onClick={() => copyId(result.interaction_id)}
                  className="btn-tool flex-shrink-0"
                  title={t('common.copyId', 'Copy ID')}
                  aria-label={t('common.copyId', 'Copy ID')}
                >
                  {copied ? <Check size={13} aria-hidden="true" className="text-emerald-300" /> : <Copy size={13} aria-hidden="true" />}
                </button>
              </dd>
            </div>

            {result.sprint_id && (
              <div className="data-row flex flex-wrap items-center gap-2">
                <dt className="w-32 text-gray-400">{t('generate.viewWorkspace', 'View workspace')}</dt>
                <dd>
                  <button
                    type="button"
                    onClick={() => navigate(`/sprints/${result.sprint_id}/live`)}
                    className="btn btn-primary btn-sm h-8"
                  >
                    {t('generate.sprintLink', { id: result.sprint_id, defaultValue: 'Sprint #{{id}}' })} <ArrowRight size={13} aria-hidden="true" />
                  </button>
                </dd>
              </div>
            )}
          </dl>
        </div>
      )}

      <Panel>
        <form onSubmit={handleSubmit} className="space-y-4 p-4">
          {/* Story */}
          <div>
            <label htmlFor="generate-story" className="input-label">{t('generate.story', 'Story / Feature request')} *</label>
            <textarea
              id="generate-story"
              value={story}
              onChange={e => setStory(e.target.value)}
              required
              rows={6}
              placeholder={t('generate.storyPlaceholder', 'As a user, I want to...')}
              className="textarea w-full text-[13px]"
            />
            <p className="text-meta mt-1">{t('generate.interactionHint', 'Be specific about the desired behavior and edge cases')}</p>
          </div>

          {/* Constraints */}
          <div>
            <label htmlFor="generate-constraints" className="input-label">{t('generate.constraints', 'Constraints')} <span className="font-normal text-gray-400">{t('common.optional', { defaultValue: '(optional)' })}</span></label>
            <textarea
              id="generate-constraints"
              value={constraints}
              onChange={e => setConstraints(e.target.value)}
              rows={3}
              placeholder={t('generate.constraintsPlaceholder', 'e.g. Must use PostgreSQL, no external APIs, TypeScript only')}
              className="textarea w-full text-[13px]"
            />
          </div>

          {error && <div role="alert" className="alert-error text-[13px]">{error}</div>}

          <div className="flex justify-end border-t border-surface-700 pt-3">
            <button
              type="submit"
              disabled={loading || !story.trim()}
              className="btn btn-primary btn-sm h-8"
            >
              {loading
                ? <Loader2 size={13} aria-hidden="true" className="animate-spin motion-reduce:animate-none" />
                : <Code2 size={13} aria-hidden="true" />}
              {loading ? t('generate.generating', 'Generating…') : t('generate.generate', 'Generate')}
            </button>
          </div>
        </form>
      </Panel>
    </div>
  )
}
