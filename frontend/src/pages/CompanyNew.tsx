import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAppStore } from '../store/useAppStore'
import { useAuthStore } from '../store/useAuthStore'
import type {
  DetectedStack,
  DiscoverTeamAgent,
  DiscoverTeamResponse,
  InitCompanyResponse,
} from '../services/api'
import { 
  useModels, 
  useDiscoverTeam, 
  useInitCompany 
} from '../hooks/useQueries'

// ── Types ────────────────────────────────────────────────────────────────────

interface Agent {
  name: string
  role: string
  persona: string
  seniority: string
  specialty: string
  token_budget?: number
  reasoning?: string
  model?: string
  system_prompt?: string
}

interface ModelInfo {
  id: string
  provider: 'gemini' | 'deepseek'
  tier: 'strategic' | 'balanced' | 'code' | 'fast' | 'preview'
  description: string
}

// Static display metadata keyed by tier — used when model ID is not in the
// live catalogue (e.g. while loading or for legacy agents).
const TIER_META: Record<string, { color: string; icon: string; label: string }> = {
  strategic: { color: 'text-blue-300 bg-blue-900/30',   icon: '🔵', label: 'Strategic' },
  balanced:  { color: 'text-cyan-300 bg-cyan-900/30',   icon: '⚡', label: 'Balanced'  },
  code:      { color: 'text-green-300 bg-green-900/30', icon: '🟢', label: 'Code'      },
  fast:      { color: 'text-yellow-300 bg-yellow-900/30', icon: '🟡', label: 'Fast'    },
  preview:   { color: 'text-purple-300 bg-purple-900/30', icon: '🔮', label: 'Preview' },
}

interface Permissions {
  git_access: string
  cloud_access: string
  db_access: string
  ci_access: string
  secrets_access: string
  require_approval_deploy: boolean
  require_approval_db_write: boolean
}

// ── Constants ────────────────────────────────────────────────────────────────

const ROLES = [
  { value: 'PO',       label: 'Product Owner',        icon: '🎯', desc: 'Defines features and priorities' },
  { value: 'SM',       label: 'Scrum Master',          icon: '🔄', desc: 'Facilitates agile delivery' },
  { value: 'Architect',label: 'Solution Architect',    icon: '🏗️', desc: 'Designs system structure' },
  { value: 'Dev',      label: 'Developer',             icon: '💻', desc: 'Writes and reviews code' },
  { value: 'QA',       label: 'QA Engineer',           icon: '🧪', desc: 'Tests and validates quality' },
  { value: 'DevOps',   label: 'DevOps Engineer',       icon: '🚀', desc: 'CI/CD and infrastructure' },
  { value: 'Security', label: 'Security Engineer',     icon: '🔐', desc: 'Vulnerability and compliance' },
  { value: 'Data',     label: 'Data Engineer',         icon: '📊', desc: 'Pipelines and analytics' },
]

const SENIORITIES = [
  { value: 'Junior', label: 'Junior',  color: 'text-gray-400',   desc: '0–2 yrs' },
  { value: 'Mid',    label: 'Mid',     color: 'text-blue-400',   desc: '2–5 yrs' },
  { value: 'Senior', label: 'Senior',  color: 'text-brand-400',  desc: '5–10 yrs' },
  { value: 'Staff',  label: 'Staff',   color: 'text-purple-400', desc: '10+ yrs' },
]

const AUTONOMY_LEVELS = [
  {
    value: 1,
    label: 'Level 1 — Copilot',
    icon: '🧑‍✈️',
    color: 'border-green-700/50 bg-green-950/20',
    badge: 'bg-green-900/40 text-green-300',
    desc: 'Every AI action requires explicit human approval. Safe for regulated environments.',
    features: ['Full human oversight', 'Manual approve/reject each task', 'Audit log for all actions'],
  },
  {
    value: 2,
    label: 'Level 2 — Exception Manager',
    icon: '⚡',
    color: 'border-brand-700/50 bg-brand-950/20',
    badge: 'bg-brand-900/40 text-brand-300',
    desc: 'AI runs routine tasks autonomously. Flags risky actions for human review.',
    features: ['Auto-approve low-risk tasks', 'Human review for deployments & DB writes', 'Configurable risk thresholds'],
  },
  {
    value: 3,
    label: 'Level 3 — Full Autonomous',
    icon: '🤖',
    color: 'border-red-700/40 bg-red-950/10',
    badge: 'bg-red-900/40 text-red-300',
    desc: 'AI operates without intervention. Recommended only after extensive testing.',
    features: ['Zero human intervention', 'Self-healing and auto-rollback', 'SLA-driven decisions'],
  },
]

const PERM_LEVELS = {
  none:      { label: 'None',       color: 'text-gray-500' },
  read_only: { label: 'Read Only',  color: 'text-blue-400' },
  write:     { label: 'Write',      color: 'text-yellow-400' },
  admin:     { label: 'Admin',      color: 'text-red-400' },
}

const DEFAULT_PERMISSIONS: Permissions = {
  git_access: 'read_only',
  cloud_access: 'none',
  db_access: 'read_only',
  ci_access: 'none',
  secrets_access: 'none',
  require_approval_deploy: true,
  require_approval_db_write: true,
}

const STEPS = ['Project', 'Company', 'Autonomy', 'Permissions', 'Team', 'Review']
const DEFAULT_PROJECT_PATH = import.meta.env.VITE_DEFAULT_PROJECT_PATH || ''

// ── Sub-components ────────────────────────────────────────────────────────────

function StepIndicator({ current, steps }: { current: number; steps: string[] }) {
  return (
    <div className="flex items-center justify-between mb-8">
      {steps.map((label, i) => (
        <div key={i} className="flex items-center flex-1 last:flex-none">
          <div className="flex flex-col items-center gap-1 flex-shrink-0">
            <div className={`w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold transition-all
              ${i < current ? 'bg-brand-600 text-white' : i === current ? 'bg-brand-500 text-white ring-2 ring-brand-400/40' : 'bg-surface-700 text-gray-500'}`}>
              {i < current ? '✓' : i + 1}
            </div>
            <span className={`text-xs whitespace-nowrap
              ${i === current ? 'text-brand-300 font-medium' : i < current ? 'text-gray-400' : 'text-gray-600'}`}>
              {label}
            </span>
          </div>
          {i < steps.length - 1 && (
            <div className={`h-px flex-1 mx-2 mb-5 transition-all ${i < current ? 'bg-brand-600' : 'bg-surface-700'}`} />
          )}
        </div>
      ))}
    </div>
  )
}

function AgentCard({ agent, index, onUpdate, onRemove, canRemove, availableModels }: {
  agent: Agent
  index: number
  onUpdate: (i: number, field: keyof Agent, val: string | number) => void
  onRemove: (i: number) => void
  canRemove: boolean
  availableModels: ModelInfo[]
}) {
  const roleInfo  = ROLES.find(r => r.value === agent.role) ?? ROLES[3]
  const senInfo   = SENIORITIES.find(s => s.value === agent.seniority) ?? SENIORITIES[2]
  const modelInfo = availableModels.find(m => m.id === (agent.model ?? ''))
  const tierMeta  = TIER_META[modelInfo?.tier ?? 'balanced']
  const [showPrompt, setShowPrompt] = useState(false)

  return (
    <div className="bg-surface-800 border border-surface-700 rounded-xl p-5 space-y-4">
      {/* Header */}
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="text-2xl">{roleInfo.icon}</span>
          <div>
            {agent.name ? (
              <>
                <p className="text-sm font-medium text-white">{agent.name}</p>
                <p className="text-xs text-gray-500">{roleInfo.label} · {roleInfo.desc}</p>
              </>
            ) : (
              <>
                <p className="text-sm font-medium text-white">{roleInfo.label}</p>
                <p className="text-xs text-gray-500">{roleInfo.desc}</p>
              </>
            )}
          </div>
        </div>
        <div className="flex items-center gap-1">
          {modelInfo && (
            <span className={`badge text-[10px] font-mono ${tierMeta.color}`}>
              {tierMeta.icon} {agent.model}
            </span>
          )}
          {canRemove && (
            <button onClick={() => onRemove(index)}
              className="text-gray-600 hover:text-red-400 transition-colors text-lg leading-none p-1 ml-1"
              title="Remove agent">×</button>
          )}
        </div>
      </div>

      {/* Name / Alias */}
      <div>
        <label className="input-label">Name / Alias</label>
        <input type="text" value={agent.name}
          onChange={e => onUpdate(index, 'name', e.target.value)}
          placeholder="e.g. Atlas, Nova, Sentinel..."
          className="input w-full" />
      </div>

      {/* Role + Seniority */}
      <div className="grid grid-cols-2 gap-2">
        <div>
          <label className="input-label">Role</label>
          <select value={agent.role} onChange={e => onUpdate(index, 'role', e.target.value)} className="select w-full">
            {ROLES.map(r => <option key={r.value} value={r.value}>{r.icon} {r.label}</option>)}
          </select>
        </div>
        <div>
          <label className="input-label">Seniority</label>
          <select value={agent.seniority} onChange={e => onUpdate(index, 'seniority', e.target.value)} className="select w-full">
            {SENIORITIES.map(s => (
              <option key={s.value} value={s.value}>{s.label} ({s.desc})</option>
            ))}
          </select>
        </div>
      </div>

      {/* Model selector */}
      <div>
        <label className="input-label">LLM Model</label>
        <select value={agent.model ?? ''}
          onChange={e => onUpdate(index, 'model', e.target.value)} className="select w-full text-xs">
          <option value="">— Select a model —</option>
          {availableModels.length === 0 && (
            <option value="" disabled>Loading models…</option>
          )}
          {Object.keys(TIER_META).map(tier => {
            const group = availableModels.filter(m => m.tier === tier)
            if (!group.length) return null
            const tm = TIER_META[tier]
            return (
              <optgroup key={tier} label={`${tm.icon} ${tm.label} — ${tier}`}>
                {group.map(m => (
                  <option key={m.id} value={m.id}>
                    {m.id} ({m.provider})
                  </option>
                ))}
              </optgroup>
            )
          })}
          {agent.model && !availableModels.find(m => m.id === agent.model) && (
            <option value={agent.model}>{agent.model} (unresolved)</option>
          )}
        </select>
        {modelInfo && (
          <p className="text-[10px] text-gray-600 mt-1">{modelInfo.description}</p>
        )}
      </div>

      {/* Specialty */}
      <div>
        <label className="input-label">Specialty / Focus area</label>
        <input type="text" value={agent.specialty || agent.persona}
          onChange={e => { onUpdate(index, 'persona', e.target.value); onUpdate(index, 'specialty', e.target.value) }}
          placeholder={`e.g. ${roleInfo.value === 'Dev' ? 'React, TypeScript, REST APIs' : roleInfo.value === 'DevOps' ? 'Docker, Kubernetes, Terraform' : roleInfo.value === 'QA' ? 'Playwright, Cypress, Load Testing' : 'your focus area'}`}
          className="input w-full" />
      </div>

      {/* System prompt — collapsible */}
      {agent.system_prompt && (
        <div>
          <button type="button"
            onClick={() => setShowPrompt(v => !v)}
            className="text-xs text-gray-500 hover:text-gray-300 flex items-center gap-1 transition-colors">
            {showPrompt ? '▾' : '▸'} System prompt
          </button>
          {showPrompt && (
            <textarea
              value={agent.system_prompt}
              onChange={e => onUpdate(index, 'system_prompt', e.target.value)}
              rows={4}
              className="textarea w-full text-xs mt-1 text-gray-300 font-mono"
            />
          )}
        </div>
      )}

      {/* Token budget */}
      <div>
        <label className="input-label">Monthly token budget</label>
        <div className="flex items-center gap-2">
          <input type="range" min={100000} max={5000000} step={100000}
            value={agent.token_budget ?? 500000}
            onChange={e => onUpdate(index, 'token_budget', parseInt(e.target.value))}
            className="flex-1 accent-brand-500" />
          <span className="text-xs text-brand-300 w-20 text-right font-mono">
            {((agent.token_budget ?? 500000) / 1000).toFixed(0)}K tokens
          </span>
        </div>
      </div>

      {/* Badges + reasoning */}
      <div className="flex gap-2 flex-wrap pt-1">
        <span className={`badge bg-surface-700 ${senInfo.color}`}>{senInfo.label}</span>
        <span className="badge bg-surface-700 text-gray-400">{roleInfo.label}</span>
        {agent.specialty && <span className="badge bg-brand-900/30 text-brand-300">{agent.specialty}</span>}
      </div>
      {agent.reasoning && (
        <p className="text-xs text-gray-500 italic border-t border-surface-700/50 pt-2 mt-1">
          💡 {agent.reasoning}
        </p>
      )}
    </div>
  )
}

function PermRow({ icon, label, desc, permKey, value, options, onChange }: {
  icon: string; label: string; desc: string
  permKey: string; value: string; options: string[]
  onChange: (key: string, val: string) => void
}) {
  const current = PERM_LEVELS[value as keyof typeof PERM_LEVELS]
  return (
    <div className="flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-4 py-3 border-b border-surface-700 last:border-0">
      <div className="flex items-center gap-3 flex-1 min-w-0">
        <span className="text-xl flex-shrink-0">{icon}</span>
        <div className="min-w-0">
          <p className="text-sm font-medium text-gray-200">{label}</p>
          <p className="text-xs text-gray-500 truncate">{desc}</p>
        </div>
      </div>
      <div className="flex items-center gap-2 flex-shrink-0">
        <span className={`text-xs font-medium ${current?.color ?? 'text-gray-400'} w-20 text-right`}>
          {current?.label}
        </span>
        <select value={value} onChange={e => onChange(permKey, e.target.value)}
          className="select w-36 text-xs">
          {options.map(o => (
            <option key={o} value={o}>{PERM_LEVELS[o as keyof typeof PERM_LEVELS]?.label ?? o}</option>
          ))}
        </select>
      </div>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export default function CompanyNew() {
  const navigate = useNavigate()
  const setCompany = useAppStore(state => state.setCompany)

  // Wizard state
  const [step, setStep] = useState(0)

  // Step 0 — Project
  const [projectPath, setProjectPath] = useState(DEFAULT_PROJECT_PATH)
  const [discoveryResult, setDiscoveryResult] = useState<DetectedStack | null>(null)

  // Step 1 — Company
  const [companyName, setCompanyName] = useState('')
  const [companyDesc, setCompanyDesc] = useState('')
  const [scopeSize, setScopeSize] = useState<'S' | 'M' | 'L'>('M')
  const [timezone, setTimezone] = useState('UTC')

  // Step 2 — Autonomy
  const [autonomyLevel, setAutonomyLevel] = useState(2)

  // Step 3 — Permissions
  const [permissions, setPermissions] = useState<Permissions>(DEFAULT_PERMISSIONS)

  // Step 4 — Team
  const [agents, setAgents] = useState<Agent[]>([
    { name: '', role: 'PO', persona: '', seniority: 'Senior', specialty: 'Product Management & Backlog', token_budget: 500000 },
    { name: '', role: 'Architect', persona: '', seniority: 'Senior', specialty: '', token_budget: 1000000 },
    { name: '', role: 'Dev', persona: '', seniority: 'Mid', specialty: '', token_budget: 500000 },
    { name: '', role: 'QA', persona: '', seniority: 'Senior', specialty: 'Automated Testing', token_budget: 300000 },
  ])

  // Available LLM models — fetched dynamically from the proxy via backend
  const { data: modelsData } = useModels()
  const availableModels = (modelsData?.models || []) as ModelInfo[]

  const discoverTeamMutation = useDiscoverTeam()
  const initCompanyMutation = useInitCompany()

  // Global
  const loading = initCompanyMutation.isPending
  const mutationError = (discoverTeamMutation.error as any)?.message || (initCompanyMutation.error as any)?.message || null

  // ── Discover ──────────────────────────────────────────────────────────────

  const handleDiscover = async () => {
    if (!projectPath.trim()) return
    setDiscoveryResult(null)
    
    discoverTeamMutation.mutate({ companyId: null, projectPath: projectPath.trim() }, {
      onSuccess: (response: DiscoverTeamResponse) => {
        setDiscoveryResult(response.detected_stack)
        if (response.detected_stack.name && !companyName) {
          setCompanyName(response.detected_stack.name)
        }
        if (response.suggested_team.length) {
          setAgents(response.suggested_team.map((a: DiscoverTeamAgent) => ({
            ...a,
            name: a.name ?? '',
            persona: a.persona ?? a.specialty ?? '',
            token_budget: a.token_budget ?? 500000,
            model: a.model ?? 'deepseek-coder',
            system_prompt: a.system_prompt ?? '',
          })))
        }
      }
    })
  }

  // ── Agent helpers ─────────────────────────────────────────────────────────

  const addAgent = () => setAgents(prev => [...prev, {
    name: '', role: 'Dev', persona: '', seniority: 'Mid', specialty: '', token_budget: 500000,
  }])

  const removeAgent = (i: number) => setAgents(prev => prev.filter((_, idx) => idx !== i))

  const updateAgent = (i: number, field: keyof Agent, val: string | number) =>
    setAgents(prev => prev.map((a, idx) => idx === i ? { ...a, [field]: val } : a))

  // ── Permission helpers ────────────────────────────────────────────────────

  const updatePerm = (key: string, val: string) =>
    setPermissions(prev => ({ ...prev, [key]: val }))

  // ── Submit ────────────────────────────────────────────────────────────────

  const handleSubmit = async () => {
    const { require_approval_deploy, require_approval_db_write, ...credPerms } = permissions
    const payload = {
      company_name: companyName.trim(),
      description: companyDesc,
      default_scope_size: scopeSize,
      autonomy_level: autonomyLevel,
      timezone,
      agents,
      permissions: credPerms,
      governance: { require_approval_deploy, require_approval_db_write },
      project_path: projectPath.trim() || undefined,
      detected_stack: discoveryResult || undefined,
    }

    initCompanyMutation.mutate(payload, {
      onSuccess: (response: InitCompanyResponse) => {
        const companyId = response.company_id
        const sprintId = response?.sprint_id ?? companyId
        const refresh = useAuthStore.getState().loadMe()
        setCompany(companyId, companyName.trim())
        navigate(`/workspace/${sprintId}`)
        void refresh.catch(() => {})
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

  const renderStep0 = () => (
    <div className="space-y-5">
      <div>
        <h2 className="text-lg font-semibold text-white mb-1">Scan your project</h2>
        <p className="text-sm text-gray-400">
          Point La Fabrique at your repository. We'll detect the stack and suggest an optimal AI team.
          You can skip this and configure manually.
        </p>
      </div>

      <div className="flex flex-col sm:flex-row gap-2">
        <input type="text" value={projectPath} onChange={e => setProjectPath(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && handleDiscover()}
          placeholder="/projects/le-fabrique  (path inside the Docker container)"
          className="input flex-1 font-mono text-sm" />
        <button type="button" onClick={async () => {
          try {
            const token = useAuthStore.getState().token
            const res = await fetch('http://localhost:8000/v1/discover/directories', {
              headers: { 'Authorization': `Bearer ${token}` }
            })
            const data = await res.json()
            if (data.directories && data.directories.length > 0) {
              const names = data.directories.map((d: { name: string; path: string }) => d.name).join('\n')
              const selected = prompt(`Directorios disponibles en ${data.root}:\n\n${names}\n\nEscribe el nombre del directorio:`)
              if (selected) {
                const dir = data.directories.find((d: { name: string; path: string }) => d.name === selected)
                if (dir) {
                  setProjectPath(dir.path)
                } else {
                  alert('Directorio no encontrado: ' + selected)
                }
              }
            } else {
              alert('No se encontraron directorios en ' + data.root)
            }
          } catch (err) {
            alert('Error al listar directorios: ' + (err as Error).message)
          }
        }}
          className="btn btn-secondary flex-shrink-0">
          📂 Examinar
        </button>
        <button type="button" onClick={handleDiscover}
          disabled={discoverTeamMutation.isPending || !projectPath.trim()}
          className="btn btn-primary flex-shrink-0 min-w-28">
          {discoverTeamMutation.isPending ? (
            <span className="flex items-center gap-2">
              <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
              Scanning…
            </span>
          ) : '🔍 Scan'}
        </button>
      </div>

      {mutationError && <div className="alert-error text-sm">{mutationError}</div>}

      {discoveryResult && (
        <div className="rounded-xl border border-brand-700/40 bg-brand-950/20 overflow-hidden">
          <div className="px-4 py-3 border-b border-brand-700/20 flex items-center gap-2">
            <span className="text-green-400 text-lg">✓</span>
            <span className="text-sm font-semibold text-brand-200">Stack detected — <span className="font-mono">{discoveryResult.name}</span></span>
          </div>
          <div className="p-4 grid grid-cols-2 lg:grid-cols-4 gap-3 text-xs">
            {[
              { label: 'Primary language', value: discoveryResult.language, icon: '🔷' },
              { label: 'Frameworks', value: discoveryResult.frameworks?.join(', ') || '—', icon: '🧩' },
              { label: 'Databases', value: discoveryResult.databases?.join(', ') || '—', icon: '🗄️' },
              { label: 'Infra', value: discoveryResult.infra?.join(', ') || '—', icon: '⚙️' },
            ].map(({ label, value, icon }) => (
              <div key={label} className="bg-surface-800/70 rounded-lg p-3">
                <span className="text-base">{icon}</span>
                <p className="text-gray-500 mt-1">{label}</p>
                <p className="text-gray-200 font-medium mt-0.5 break-words leading-snug" title={value}>{value}</p>
              </div>
            ))}
          </div>
          <div className="px-4 py-2 border-t border-brand-700/20 flex items-center justify-between text-xs">
            <span className="text-gray-500">{discoveryResult.file_count} files scanned</span>
            <div className="flex items-center gap-2">
              {discoveryResult.team_source === 'ai' && (
                <span className="badge bg-purple-900/40 text-purple-300">✨ AI team suggestion</span>
              )}
              {discoveryResult.team_source === 'rules' && (
                <span className="badge bg-surface-700 text-gray-400">rule-based suggestion</span>
              )}
              <span className={`badge ${discoveryResult.complexity === 'High' ? 'bg-red-900/40 text-red-300' : discoveryResult.complexity === 'Medium' ? 'bg-yellow-900/40 text-yellow-300' : 'bg-green-900/40 text-green-300'}`}>
                {discoveryResult.complexity} complexity
              </span>
            </div>
          </div>
        </div>
      )}

      {!discoveryResult && (
        <div className="rounded-xl border border-surface-700 bg-surface-800/30 p-5 text-center">
          <p className="text-3xl mb-2">🏗️</p>
          <p className="text-sm text-gray-400">No project scanned yet.</p>
          <p className="text-xs text-gray-600 mt-1">You can also configure the team manually in Step 5.</p>
        </div>
      )}
    </div>
  )

  const renderStep1 = () => (
    <div className="space-y-5">
      <div>
        <h2 className="text-lg font-semibold text-white mb-1">Company details</h2>
        <p className="text-sm text-gray-400">Basic information about your AI software factory.</p>
      </div>

      <div>
        <label className="input-label">Company / Factory name *</label>
        <input type="text" value={companyName} onChange={e => setCompanyName(e.target.value)}
          placeholder="Acme Corp" required className="input w-full text-base" autoFocus />
        {companyName.length > 0 && companyName.length < 2 && (
          <p className="text-xs text-red-400 mt-1">Name must be at least 2 characters.</p>
        )}
      </div>

      <div>
        <label className="input-label">Description <span className="text-gray-600 normal-case font-normal">(optional)</span></label>
        <textarea value={companyDesc} onChange={e => setCompanyDesc(e.target.value)}
          placeholder="A brief description of what this factory builds…"
          rows={3} className="textarea w-full" />
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div>
          <label className="input-label">Default sprint scope</label>
          <select
            value={scopeSize}
            onChange={e => setScopeSize(e.target.value as 'S' | 'M' | 'L')}
            className="select w-full"
          >
            <option value="S">Small — up to 4 tasks, single concern</option>
            <option value="M">Medium — up to 8 tasks, one full feature</option>
            <option value="L">Large — up to 12 tasks, requires review</option>
          </select>
          <p className="text-xs text-gray-500 mt-1">
            {scopeSize === 'S' ? 'Tight scope — ideal for focused AI passes' : scopeSize === 'M' ? 'Recommended default — balances scope and AI focus' : 'Wide scope — the system may suggest splitting before execution'}
          </p>
        </div>
        <div>
          <label className="input-label">Timezone</label>
          <select value={timezone} onChange={e => setTimezone(e.target.value)} className="select w-full">
            {['UTC','America/New_York','America/Chicago','America/Denver','America/Los_Angeles',
              'America/Sao_Paulo','America/Argentina/Buenos_Aires','Europe/London','Europe/Paris',
              'Europe/Berlin','Asia/Tokyo','Asia/Singapore','Australia/Sydney'].map(tz => (
              <option key={tz} value={tz}>{tz}</option>
            ))}
          </select>
        </div>
      </div>
    </div>
  )

  const renderStep2 = () => (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold text-white mb-1">Autonomy level</h2>
        <p className="text-sm text-gray-400">How much authority does the AI have to act without human approval?</p>
      </div>

      <div className="space-y-3">
        {AUTONOMY_LEVELS.map(level => (
          <button key={level.value} type="button"
            onClick={() => setAutonomyLevel(level.value)}
            className={`w-full text-left rounded-xl border-2 p-4 transition-all ${autonomyLevel === level.value ? level.color + ' border-opacity-100' : 'border-surface-700 bg-surface-800/30 hover:border-surface-600'}`}>
            <div className="flex items-start gap-3">
              <span className="text-2xl flex-shrink-0 mt-0.5">{level.icon}</span>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-semibold text-white text-sm">{level.label}</span>
                  {autonomyLevel === level.value && (
                    <span className={`badge text-xs ${level.badge}`}>Selected</span>
                  )}
                </div>
                <p className="text-xs text-gray-400 mt-1">{level.desc}</p>
                <ul className="mt-2 space-y-0.5">
                  {level.features.map(f => (
                    <li key={f} className="text-xs text-gray-500 flex items-center gap-1.5">
                      <span className="text-brand-400">·</span> {f}
                    </li>
                  ))}
                </ul>
              </div>
              <div className={`w-5 h-5 rounded-full border-2 flex-shrink-0 mt-0.5 flex items-center justify-center ${autonomyLevel === level.value ? 'border-brand-400 bg-brand-600' : 'border-surface-600'}`}>
                {autonomyLevel === level.value && <span className="text-white text-xs">✓</span>}
              </div>
            </div>
          </button>
        ))}
      </div>
    </div>
  )

  const renderStep3 = () => {
    const gitOptions    = ['read_only', 'write', ...(autonomyLevel === 3 ? ['admin'] : [])]
    const cloudOptions  = ['none', 'read_only', ...(autonomyLevel >= 2 ? ['write'] : []), ...(autonomyLevel === 3 ? ['admin'] : [])]
    const dbOptions     = ['read_only', 'write', ...(autonomyLevel === 3 ? ['admin'] : [])]
    const ciOptions     = ['none', 'read_only', ...(autonomyLevel >= 2 ? ['write'] : [])]
    const secretOptions = ['none', ...(autonomyLevel >= 2 ? ['read_only'] : []), ...(autonomyLevel === 3 ? ['write'] : [])]

    return (
      <div className="space-y-5">
        <div>
          <h2 className="text-lg font-semibold text-white mb-1">Infrastructure permissions</h2>
          <p className="text-sm text-gray-400">
            Define what the AI agents are allowed to access. Available options scale with the autonomy level.
          </p>
        </div>

        <div className="card divide-y divide-surface-700 rounded-xl overflow-hidden">
          <PermRow icon="🔀" label="Git / Version Control" permKey="git_access" value={permissions.git_access}
            options={gitOptions} onChange={updatePerm}
            desc="Clone repos, push branches, create PRs, merge, tag releases" />
          <PermRow icon="☁️" label="Cloud Infrastructure" permKey="cloud_access" value={permissions.cloud_access}
            options={cloudOptions} onChange={updatePerm}
            desc="List, deploy, scale, or destroy cloud resources (AWS / GCP / Azure)" />
          <PermRow icon="🗄️" label="Database" permKey="db_access" value={permissions.db_access}
            options={dbOptions} onChange={updatePerm}
            desc="SELECT queries, INSERT/UPDATE, or DROP/TRUNCATE operations" />
          <PermRow icon="🔧" label="CI/CD Pipeline" permKey="ci_access" value={permissions.ci_access}
            options={ciOptions} onChange={updatePerm}
            desc="Trigger builds, read pipeline logs, cancel/retry runs" />
          <PermRow icon="🔑" label="Secrets & Credentials" permKey="secrets_access" value={permissions.secrets_access}
            options={secretOptions} onChange={updatePerm}
            desc="Read API keys, rotate secrets, manage vault entries" />
        </div>

        <div className="card p-4 space-y-3">
          <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider">Governance gates</p>
          {[
            { key: 'require_approval_deploy', label: 'Require human approval before any deployment', icon: '🚀' },
            { key: 'require_approval_db_write', label: 'Require human approval for database writes', icon: '🗄️' },
          ].map(({ key, label, icon }) => (
            <label key={key} className="flex items-center gap-3 cursor-pointer group">
              <input type="checkbox"
                checked={permissions[key as keyof Permissions] as boolean}
                onChange={e => setPermissions(prev => ({ ...prev, [key]: e.target.checked }))}
                className="w-4 h-4 rounded accent-brand-500 flex-shrink-0" />
              <span className="text-sm">
                <span className="mr-1.5">{icon}</span>
                <span className="text-gray-300 group-hover:text-white transition-colors">{label}</span>
              </span>
            </label>
          ))}
        </div>

        {autonomyLevel === 1 && (
          <div className="alert-info text-xs">
            <span>ℹ️</span>
            <span>At Autonomy Level 1, all AI actions already require manual approval. Governance gates are enforced system-wide.</span>
          </div>
        )}
      </div>
    )
  }

  const renderStep4 = () => (
    <div className="space-y-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold text-white mb-1">Build your AI team</h2>
          <p className="text-sm text-gray-400">
            {discoveryResult ? 'Team suggested based on detected stack. Customize as needed.' : 'Configure the AI agents that will work in your factory.'}
          </p>
        </div>
        <button type="button" onClick={addAgent} className="btn btn-secondary btn-sm flex-shrink-0">
          + Add agent
        </button>
      </div>

      {agents.length === 0 && (
        <div className="alert-error text-sm">At least 1 agent is required.</div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        {agents.map((agent, i) => (
          <AgentCard key={i} agent={agent} index={i}
            onUpdate={updateAgent} onRemove={removeAgent} canRemove={agents.length > 1}
            availableModels={availableModels} />
        ))}
      </div>

      <div className="rounded-lg bg-surface-800/30 border border-surface-700 p-3 text-xs text-gray-500 space-y-1">
        <p className="font-medium text-gray-400">Team token budget</p>
        <p>Total monthly: <span className="text-brand-300 font-mono">
          {(agents.reduce((s, a) => s + (a.token_budget ?? 500000), 0) / 1000000).toFixed(1)}M tokens
        </span></p>
        <p className="text-gray-600">Budgets are soft limits — alerts fire at 80% usage. Hard caps can be set post-creation.</p>
      </div>
    </div>
  )

  const renderStep5 = () => {
    const selectedAutonomy = AUTONOMY_LEVELS.find(l => l.value === autonomyLevel)!
    const totalBudget = agents.reduce((s, a) => s + (a.token_budget ?? 500000), 0)
    return (
      <div className="space-y-5">
        <div>
          <h2 className="text-lg font-semibold text-white mb-1">Review & initialize</h2>
          <p className="text-sm text-gray-400">Everything looks good? Hit Initialize to create your factory.</p>
        </div>

        <div className="space-y-3">
          {/* Company */}
          <div className="card p-4">
            <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">Company</p>
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="font-semibold text-white">{companyName}</p>
                {companyDesc && <p className="text-sm text-gray-400 mt-0.5">{companyDesc}</p>}
              </div>
              <button type="button" onClick={() => setStep(1)} className="text-xs text-brand-400 hover:underline flex-shrink-0">Edit</button>
            </div>
            <div className="mt-2 flex gap-3 text-xs text-gray-500">
              <span>🔄 Scope {scopeSize} sprints</span>
              <span>🌍 {timezone}</span>
            </div>
          </div>

          {/* Autonomy */}
          <div className="card p-4">
            <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">Autonomy</p>
            <div className="flex items-center justify-between gap-4">
              <div className="flex items-center gap-2">
                <span className="text-xl">{selectedAutonomy.icon}</span>
                <span className="text-sm font-medium text-white">{selectedAutonomy.label}</span>
                <span className={`badge text-xs ${selectedAutonomy.badge}`}>Level {autonomyLevel}</span>
              </div>
              <button type="button" onClick={() => setStep(2)} className="text-xs text-brand-400 hover:underline">Edit</button>
            </div>
          </div>

          {/* Permissions */}
          <div className="card p-4">
            <div className="flex items-center justify-between mb-2">
              <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Permissions</p>
              <button type="button" onClick={() => setStep(3)} className="text-xs text-brand-400 hover:underline">Edit</button>
            </div>
            <div className="grid grid-cols-5 gap-2">
              {[
                { label: 'Git', key: 'git_access', icon: '🔀' },
                { label: 'Cloud', key: 'cloud_access', icon: '☁️' },
                { label: 'DB', key: 'db_access', icon: '🗄️' },
                { label: 'CI/CD', key: 'ci_access', icon: '🔧' },
                { label: 'Secrets', key: 'secrets_access', icon: '🔑' },
              ].map(({ label, key, icon }) => {
                const val = permissions[key as keyof Permissions] as string
                const perm = PERM_LEVELS[val as keyof typeof PERM_LEVELS]
                return (
                  <div key={key} className="bg-surface-800/60 rounded-lg p-2 text-center">
                    <span className="text-base">{icon}</span>
                    <p className="text-xs text-gray-500 mt-0.5">{label}</p>
                    <p className={`text-xs font-medium mt-0.5 ${perm?.color ?? 'text-gray-400'}`}>{perm?.label}</p>
                  </div>
                )
              })}
            </div>
          </div>

          {/* Team */}
          <div className="card p-4">
            <div className="flex items-center justify-between mb-2">
              <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Team — {agents.length} agents</p>
              <button type="button" onClick={() => setStep(4)} className="text-xs text-brand-400 hover:underline">Edit</button>
            </div>
            <div className="space-y-1.5">
              {agents.map((a, i) => {
                const roleInfo = ROLES.find(r => r.value === a.role)
                return (
                  <div key={i} className="flex items-center gap-2 text-sm">
                    <span>{roleInfo?.icon ?? '🤖'}</span>
                    <span className="text-gray-300 font-medium">
                      {a.name ? `${a.name} — ${roleInfo?.label ?? a.role}` : (roleInfo?.label ?? a.role)}
                    </span>
                    <span className={`text-xs ${SENIORITIES.find(s => s.value === a.seniority)?.color ?? 'text-gray-400'}`}>{a.seniority}</span>
                    {a.model && (() => {
                      const mi = availableModels.find(m => m.id === a.model)
                      const tm = TIER_META[mi?.tier ?? 'balanced']
                      return (
                        <span className={`badge text-[10px] font-mono ${tm.color}`}>
                          {tm.icon} {a.model}
                        </span>
                      )
                    })()}
                    {a.specialty && <span className="text-xs text-gray-600 truncate">· {a.specialty}</span>}
                  </div>
                )
              })}
            </div>
            <p className="text-xs text-gray-600 mt-2">
              Total budget: <span className="text-brand-300 font-mono">{(totalBudget / 1000000).toFixed(1)}M tokens/mo</span>
            </p>
          </div>
        </div>

        {mutationError && <div className="alert-error text-sm">{mutationError}</div>}

        <button type="button" onClick={handleSubmit}
          disabled={loading}
          className="btn btn-primary w-full text-base py-3">
          {loading ? (
            <span className="flex items-center gap-2 justify-center">
              <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
              Initializing factory…
            </span>
          ) : '🏭 Initialize Factory'}
        </button>
      </div>
    )
  }

  const STEP_RENDERERS = [renderStep0, renderStep1, renderStep2, renderStep3, renderStep4, renderStep5]

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="page-container max-w-6xl">
      <div className="mb-6">
        <h1 className="page-title">🏭 New Factory</h1>
        <p className="text-gray-400 text-sm mt-1">Set up your AI-powered software factory in 6 steps.</p>
      </div>

      <StepIndicator current={step} steps={STEPS} />

      <div className="card p-6">
        {STEP_RENDERERS[step]()}
      </div>

      {/* Navigation */}
      <div className="flex items-center justify-between mt-4">
        <button type="button" onClick={() => setStep(s => s - 1)}
          disabled={step === 0}
          className="btn btn-secondary disabled:opacity-30">
          ← Back
        </button>

        <span className="text-xs text-gray-600">{step + 1} / {STEPS.length}</span>

        {step < STEPS.length - 1 ? (
          <button type="button" onClick={() => setStep(s => s + 1)}
            disabled={!canProceed()}
            className="btn btn-primary disabled:opacity-50">
            Next →
          </button>
        ) : null}
      </div>
    </div>
  )
}
