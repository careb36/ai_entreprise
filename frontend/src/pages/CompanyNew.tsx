// The "New company" wizard. Its steps, cards and state hooks live in ./companyNew/.
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAppStore } from '../store/useAppStore'
import { useAuthStore } from '../store/useAuthStore'
import {
  type DiscoverTeamAgent,
  type DiscoverTeamResponse,
  type InitCompanyResponse,
} from '../services/api'
import { gitChoiceFor, useFolderGitChoice } from '../components/GitWorkflowPanel'
import {
  useModels,
  useDiscoverTeam,
  useInitCompany,
} from '../hooks/useQueries'
import { DEFAULT_PERMISSIONS, STEPS } from './companyNew/constants'
import type { ModelInfo, Permissions } from './companyNew/types'
import { StepIndicator } from './companyNew/StepIndicator'
import { useProjectFolder } from './companyNew/useProjectFolder'
import { useTeamDrafts } from './companyNew/useTeamDrafts'
import { ProjectStep } from './companyNew/ProjectStep'
import { CompanyStep } from './companyNew/CompanyStep'
import { AutonomyStep } from './companyNew/AutonomyStep'
import { PermissionsStep } from './companyNew/PermissionsStep'
import { TeamStep } from './companyNew/TeamStep'
import { ReviewStep } from './companyNew/ReviewStep'

export default function CompanyNew() {
  const { t, i18n } = useTranslation()
  const steps = STEPS.map(step => t(`companyNew.steps.${step}`, { defaultValue: step }))
  const navigate = useNavigate()
  const setCompany = useAppStore(state => state.setCompany)

  const [step, setStep] = useState(0)
  // Step 0 — Project
  const folder = useProjectFolder()
  const { projectPath, setProjectPath, discoveryResult, setDiscoveryResult, inspected, setInspected, inspectPath, folderMissing } = folder

  // Step 1 — Company
  const [companyName, setCompanyName] = useState('')
  const [companyDesc, setCompanyDesc] = useState('')
  const [scopeSize, setScopeSize] = useState<'S' | 'M' | 'L'>('M')
  const [timezone, setTimezone] = useState('UTC')

  // Step 2 — Autonomy
  const [autonomyLevel, setAutonomyLevel] = useState(2)
  const [startNow, setStartNow] = useState(false)
  const [gitChoice, setGitChoice] = useFolderGitChoice(inspected?.path ?? projectPath.trim()) // null: as the folder's inspection observed

  // Step 3 — Permissions
  const [permissions, setPermissions] = useState<Permissions>(DEFAULT_PERMISSIONS)

  // Step 4 — Team
  const { agents, setAgents, addAgent, removeAgent, updateAgent } = useTeamDrafts(t)

  // Available LLM models — fetched dynamically from the proxy via backend
  const { data: modelsData } = useModels()
  const availableModels = (modelsData?.models || []) as ModelInfo[]

  const discoverTeamMutation = useDiscoverTeam()
  const initCompanyMutation = useInitCompany()

  // Global
  const loading = initCompanyMutation.isPending
  useEffect(() => {
    if (!loading) return
    const timer = setTimeout(() => {
      console.warn('Init company timeout - resetting')
      initCompanyMutation.reset()
    }, 30000)
    return () => clearTimeout(timer)
  }, [loading])
  const mutationError = (discoverTeamMutation.error as any)?.message || (initCompanyMutation.error as any)?.message || null

  // ── Discover ──────────────────────────────────────────────────────────────

  const handleDiscover = async () => {
    let path = projectPath.trim()
    if (!path || discoverTeamMutation.isPending) return
    if (inspected?.path !== path) {
      // An inspection error (e.g. 503, daemon down) does not block scanning a typed path.
      const outcome = await inspectPath(path)
      if (outcome.kind === 'stale') return
      if (outcome.kind === 'ok') {
        if (!outcome.result.exists || !outcome.result.is_dir) return
        path = outcome.result.path
      }
    } else if (folderMissing) {
      return
    }
    setDiscoveryResult(null)

    discoverTeamMutation.mutate({ companyId: null, projectPath: path }, {
      onSuccess: (response: DiscoverTeamResponse) => {
        if (response.project_path) setProjectPath(response.project_path)
        if (response.project) setInspected(response.project)
        setDiscoveryResult(response.detected_stack)
        if (response.detected_stack.name && !companyName) {
          setCompanyName(response.detected_stack.name)
        }
        if (response.suggested_team.length) {
          setAgents(response.suggested_team.map((a: DiscoverTeamAgent) => ({
            ...a,
            name: a.name ?? '',
            persona: a.persona ?? a.specialty ?? '',
            token_budget: a.token_budget ?? 1000000,
            model: a.model ?? '',
            system_prompt: a.system_prompt ?? '',
          })))
        }
      }
    })
  }

  // ── Permission helpers ────────────────────────────────────────────────────

  const updatePerm = (key: string, val: string) =>
    setPermissions(prev => ({ ...prev, [key]: val }))

  const handleSubmit = async () => {
    const { require_approval_deploy, require_approval_db_write, ...credPerms } = permissions
    const payload = {
      company_name: companyName.trim(),
      description: companyDesc,
      default_scope_size: scopeSize,
      autonomy_level: autonomyLevel,
      start_now: startNow && autonomyLevel > 1,
      timezone,
      agents,
      permissions: credPerms,
      governance: { require_approval_deploy, require_approval_db_write },
      project_path: projectPath.trim() || undefined,
      detected_stack: discoveryResult || undefined,
      ...gitChoiceFor(inspected, gitChoice),
      // The documentation packs follow the creator's interface language; editable later.
      doc_language: (i18n.language ?? '').startsWith('es') ? 'es' as const : 'en' as const,
    }

    initCompanyMutation.mutate(payload, {
      onSuccess: (response: InitCompanyResponse) => {
        const companyId = response.company_id
        const sprintId = response?.sprint_id ?? companyId
        setCompany(companyId, companyName.trim())
        navigate(`/sprints/${sprintId}`)
        setTimeout(() => {
          if (!window.location.pathname.startsWith(`/sprints/${sprintId}`)) {
            window.location.href = `/sprints/${sprintId}`
          }
        }, 1000)
        useAuthStore.getState().loadMe().catch(() => {})
      },
      onError: (error: any) => {
        console.error('Init company failed:', error)
        alert(t('companyNew.initializationError', { message: error?.message || t('companyNew.unknownError') }))
      }
    })
  }

  // ── Validation ────────────────────────────────────────────────────────────

  const canProceed = () => {
    if (step === 0) return !discoverTeamMutation.isPending
    if (step === 1) return companyName.trim().length >= 2
    if (step === 4) return agents.length >= 1
    return true
  }

  // ── Step renderers ────────────────────────────────────────────────────────

  const STEP_RENDERERS = [
    () => (
      <ProjectStep folder={folder} discoveryResult={discoveryResult} discoverPending={discoverTeamMutation.isPending}
        handleDiscover={handleDiscover} mutationError={mutationError} />
    ),
    () => (
      <CompanyStep companyName={companyName} setCompanyName={setCompanyName} companyDesc={companyDesc}
        setCompanyDesc={setCompanyDesc} scopeSize={scopeSize} setScopeSize={setScopeSize}
        timezone={timezone} setTimezone={setTimezone} />
    ),
    () => <AutonomyStep autonomyLevel={autonomyLevel} setAutonomyLevel={setAutonomyLevel} />,
    () => (
      <PermissionsStep autonomyLevel={autonomyLevel} permissions={permissions} updatePerm={updatePerm}
        setPermissions={setPermissions} />
    ),
    () => (
      <TeamStep discoveryResult={discoveryResult} agents={agents} addAgent={addAgent} updateAgent={updateAgent}
        removeAgent={removeAgent} availableModels={availableModels} />
    ),
    () => (
      <ReviewStep companyName={companyName} companyDesc={companyDesc} scopeSize={scopeSize} timezone={timezone}
        autonomyLevel={autonomyLevel} startNow={startNow} setStartNow={setStartNow} inspected={inspected}
        gitChoice={gitChoice} setGitChoice={setGitChoice} permissions={permissions} agents={agents}
        availableModels={availableModels} mutationError={mutationError} loading={loading}
        handleSubmit={handleSubmit} setStep={setStep} />
    ),
  ]

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="page-container max-w-6xl">
      <div className="mb-6">
        <h1 className="page-title">🏭 {t('companyNew.newFactory')}</h1>
        <p className="text-gray-400 text-sm mt-1">{t('companyNew.wizardDescription')}</p>
      </div>

      <StepIndicator current={step} steps={steps} />

      <div className="card p-6">
        {STEP_RENDERERS[step]()}
      </div>

      {/* Navigation */}
      <div className="flex items-center justify-between mt-4">
        <button type="button" onClick={() => setStep(s => s - 1)}
          disabled={step === 0}
          className="btn btn-secondary disabled:opacity-30">
          ← {t('companyNew.back')}
        </button>

        <span className="text-xs text-gray-600">{step + 1} / {STEPS.length}</span>

        {step < STEPS.length - 1 ? (
          <button type="button" onClick={() => setStep(s => s + 1)}
            disabled={!canProceed()}
            className="btn btn-primary disabled:opacity-50">
            {t('companyNew.next')} →
          </button>
        ) : null}
      </div>
    </div>
  )
}
