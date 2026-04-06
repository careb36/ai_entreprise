import { useMemo, useState } from 'react'
import { useLocation, useParams } from 'react-router-dom'
import GlassRoom from '../components/GlassRoom'

export default function Glassroom() {
  const { sprint_id } = useParams()
  const location = useLocation()

  const querySprintId = useMemo(() => {
    const params = new URLSearchParams(location.search)
    return params.get('sprint_id') || params.get('sprintId')
  }, [location.search])

  const [sprintId, setSprintId] = useState(sprint_id || querySprintId || '1')

  return (
    <div className="h-[calc(100vh-3.5rem)] p-4 bg-surface-950 text-white">
      <div className="flex items-center gap-3 mb-3">
        <h1 className="text-lg font-semibold text-gray-200">Glassroom</h1>
        <div className="ml-auto flex items-center gap-2">
          <label className="text-xs text-gray-400">Sprint ID</label>
          <input
            type="text"
            value={sprintId}
            onChange={e => setSprintId(e.target.value.trim() || '1')}
            className="w-28 rounded-md border border-surface-700 bg-surface-900 px-2 py-1 text-sm text-gray-100"
            placeholder="1"
          />
        </div>
      </div>

      <div className="h-[calc(100%-3rem)] card overflow-hidden">
        <GlassRoom sprintId={sprintId} />
      </div>
    </div>
  )
}
