import { useState } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { AlertTriangle, Building2 } from 'lucide-react'
import { useAppStore } from '../store/useAppStore'
import { useStartSprint } from '../hooks/useQueries'
import { useToast } from '../store/toastStore'

interface BacklogItemDraft {
  title: string
  description: string
}

export default function SprintBoard() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { companyId, setActiveSprint } = useAppStore()
  const startSprint = useStartSprint()
  const toast = useToast()

  const [sprintName, setSprintName]   = useState('')
  const [startDate]                   = useState(new Date().toISOString().split('T')[0])
  const [scopeSize, setScopeSize]     = useState<'S' | 'M' | 'L'>('M')
  const [items, setItems]             = useState<BacklogItemDraft[]>([])

  if (!companyId) {
    return (
      <div className="max-w-lg mx-auto py-16 px-4 text-center">
        <div className="w-14 h-14 rounded-xl bg-surface-800 border border-surface-700 flex items-center justify-center mx-auto mb-4">
          <Building2 size={24} className="text-brand-400" />
        </div>
        <h2 className="text-lg font-semibold text-white mb-2">{t('glassroom.noCompanySetup')}</h2>
        <p className="text-sm text-gray-500 mb-6">{t('dashboard.noCompanyDesc')}</p>
        <Link to="/companies/new" className="btn btn-primary">{t('nav.newCompany')}</Link>
      </div>
    )
  }

  function addItem() {
    setItems(prev => [...prev, { title: '', description: '' }])
  }

  function updateItem(index: number, field: keyof BacklogItemDraft, value: string) {
    setItems(prev => prev.map((it, i) => i === index ? { ...it, [field]: value } : it))
  }

  function removeItem(index: number) {
    setItems(prev => prev.filter((_, i) => i !== index))
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    startSprint.mutate(
      {
        company_id: companyId,
        sprint_name: sprintName,
        start_date: startDate,
        scope_size: scopeSize,
        backlog_items: items,
      },
      {
        onSuccess: (res) => {
          const sprintId = res?.sprint_id
          if (sprintId) {
            setActiveSprint(sprintId)
            toast.success(t('sprintBoard.success'))
            navigate(`/sprints/${sprintId}/live`)
          }
        },
        onError: (err) => {
          toast.error(err instanceof Error ? err.message : t('sprintBoard.error'))
        },
      }
    )
  }

  return (
    <div className="max-w-2xl mx-auto py-8 px-4">
      <h1 className="text-2xl font-bold text-white mb-1">{t('sprintBoard.title')}</h1>
      <p className="text-gray-500 mb-6">{t('sprintBoard.subtitle')}</p>

      <form onSubmit={handleSubmit} className="space-y-5">
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="input-label">{t('sprintBoard.sprintName')}</label>
            <input
              type="text"
              value={sprintName}
              onChange={e => setSprintName(e.target.value)}
              required
              placeholder={t('sprintBoard.sprintNamePlaceholder')}
              className="input w-full mt-1"
            />
          </div>
          <div>
            <label className="input-label">{t('controlPanel.scope')}</label>
            <select
              value={scopeSize}
              onChange={e => setScopeSize(e.target.value as 'S' | 'M' | 'L')}
              className="input w-full mt-1"
            >
              <option value="S">{t('controlPanel.scopeS')}</option>
              <option value="M">{t('controlPanel.scopeM')}</option>
              <option value="L">{t('controlPanel.scopeL')}</option>
            </select>
          </div>
        </div>

        <div>
          <label className="input-label">{t('sprintBoard.backlogItems')}</label>
          <div className="mt-2 space-y-3">
            {items.map((item, index) => (
              <div key={index} className="card p-3 space-y-2">
                <div className="flex justify-between items-center">
                  <span className="text-xs font-medium text-gray-400">
                    {t('kanban.item')} {index + 1}
                  </span>
                  <button
                    type="button"
                    onClick={() => removeItem(index)}
                    className="text-xs text-red-400 hover:text-red-300 transition-colors"
                  >
                    {t('sprintBoard.removeItem')}
                  </button>
                </div>
                <input
                  type="text"
                  value={item.title}
                  onChange={e => updateItem(index, 'title', e.target.value)}
                  placeholder={t('sprintBoard.itemPlaceholder')}
                  className="input w-full text-sm"
                />
                <textarea
                  value={item.description}
                  onChange={e => updateItem(index, 'description', e.target.value)}
                  placeholder={t('generate.constraintsPlaceholder')}
                  rows={2}
                  className="textarea w-full text-sm"
                />
              </div>
            ))}
          </div>
          <button
            type="button"
            onClick={addItem}
            className="mt-3 text-sm text-brand-400 hover:text-brand-300 transition-colors"
          >
            {t('sprintBoard.addItem')}
          </button>
        </div>

        {startSprint.isError && (
          <div className="flex items-center gap-2 p-3 rounded-lg bg-red-950/30 border border-red-800/40 text-red-400 text-sm">
            <AlertTriangle size={14} />
            {startSprint.error instanceof Error
              ? startSprint.error.message
              : t('sprintBoard.error')}
          </div>
        )}

        <button
          type="submit"
          disabled={startSprint.isPending}
          className="btn btn-primary w-full"
        >
          {startSprint.isPending ? t('sprintBoard.starting') : t('sprintBoard.start')}
        </button>
      </form>
    </div>
  )
}
