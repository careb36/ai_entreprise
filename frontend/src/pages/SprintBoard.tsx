import { useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAppStore } from '../store/useAppStore'
import { api } from '../services/api'

interface BacklogItem {
  title: string
  description: string
}

export default function SprintBoard() {
  const { t } = useTranslation()
  const { id } = useParams()
  const navigate = useNavigate()
  const setActiveSprint = useAppStore(state => state.setActiveSprint)
  const [sprintName, setSprintName] = useState('')
  const [startDate, setStartDate] = useState(new Date().toISOString().split('T')[0])
  const [durationDays, setDurationDays] = useState(14)
  const [backlogItems, setBacklogItems] = useState<BacklogItem[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function addBacklogItem() {
    setBacklogItems([...backlogItems, { title: '', description: '' }])
  }

  function updateBacklogItem(index: number, field: keyof BacklogItem, value: string) {
    const newItems = [...backlogItems]
    newItems[index][field] = value
    setBacklogItems(newItems)
  }

  function removeBacklogItem(index: number) {
    setBacklogItems(backlogItems.filter((_, i) => i !== index))
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setLoading(true)
    setError(null)
    try {
      const response = await api.post<any>('/v1/start_sprint', {
        company_id: 1,
        sprint_name: sprintName,
        start_date: startDate,
        duration_days: durationDays,
        backlog_items: backlogItems
      })
      const sprintId = response?.sprint_id
      if (sprintId) {
        setActiveSprint(sprintId)
        navigate(`/workspace/${sprintId}`)
      }
    } catch (err) {
      console.error('Failed to start sprint:', err)
      setError(t('sprintBoard.error'))
    }
    setLoading(false)
  }

  return (
    <div className="max-w-4xl mx-auto py-8 px-4">
      <h1 className="text-2xl font-bold text-gray-900 mb-2">
        {id ? `Sprint ${id}` : t('sprintBoard.title')}
      </h1>
      <p className="text-gray-500 mb-6">{t('sprintBoard.subtitle')}</p>

      <form onSubmit={handleSubmit} className="space-y-6">
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium text-gray-700">{t('sprintBoard.sprintName')}</label>
            <input
              type="text"
              value={sprintName}
              onChange={e => setSprintName(e.target.value)}
              required
              placeholder={t('sprintBoard.sprintNamePlaceholder')}
              className="mt-1 block w-full rounded-md border-gray-300 shadow-sm p-2 border"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700">Duration (days)</label>
            <input
              type="number"
              value={durationDays}
              onChange={e => setDurationDays(parseInt(e.target.value))}
              min={1}
              className="mt-1 block w-full rounded-md border-gray-300 shadow-sm p-2 border"
            />
          </div>
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700">Start Date</label>
          <input
            type="date"
            value={startDate}
            onChange={e => setStartDate(e.target.value)}
            required
            className="mt-1 block w-full rounded-md border-gray-300 shadow-sm p-2 border"
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-2">{t('sprintBoard.backlogItems')}</label>
          {backlogItems.map((item, index) => (
            <div key={index} className="mb-4 p-4 bg-gray-50 rounded-lg">
              <div className="flex justify-between mb-2">
                <span className="text-sm font-medium text-gray-700">Item {index + 1}</span>
                <button
                  type="button"
                  onClick={() => removeBacklogItem(index)}
                  className="text-red-600 hover:text-red-800 text-sm"
                >
                  {t('sprintBoard.removeItem')}
                </button>
              </div>
              <input
                type="text"
                value={item.title}
                onChange={e => updateBacklogItem(index, 'title', e.target.value)}
                placeholder={t('sprintBoard.itemPlaceholder')}
                className="w-full mb-2 rounded-md border-gray-300 shadow-sm p-2 border"
              />
              <textarea
                value={item.description}
                onChange={e => updateBacklogItem(index, 'description', e.target.value)}
                placeholder="Description"
                rows={2}
                className="w-full rounded-md border-gray-300 shadow-sm p-2 border"
              />
            </div>
          ))}
          <button
            type="button"
            onClick={addBacklogItem}
            className="text-indigo-600 hover:text-indigo-800"
          >
            {t('sprintBoard.addItem')}
          </button>
        </div>

        {error && (
          <div className="p-3 bg-red-50 border border-red-200 rounded-md text-red-600 text-sm">
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={loading}
          className="w-full bg-indigo-600 text-white py-2 px-4 rounded-md hover:bg-indigo-700 disabled:opacity-50"
        >
          {loading ? t('sprintBoard.starting') : t('sprintBoard.start')}
        </button>
      </form>
    </div>
  )
}
