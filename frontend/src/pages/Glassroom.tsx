import { useState, useEffect, useRef } from 'react'
import { useParams } from 'react-router-dom'
import { useSSE } from '../hooks/useSSE'

interface Event {
  timestamp: string
  type: string
  data: any
}

export default function Glassroom() {
  const { sprint_id } = useParams()
  const [sprintId, setSprintId] = useState(sprint_id || '1')
  const [events, setEvents] = useState<Event[]>([])
  const eventsEndRef = useRef<HTMLDivElement>(null)

  const { isConnected, error } = useSSE(
    `http://localhost:8000/v1/glassroom/stream?sprint_id=${sprintId}`,
    (event) => {
      setEvents(prev => [...prev, { timestamp: new Date().toISOString(), type: 'event', data: event }])
    }
  )

  useEffect(() => {
    eventsEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [events])

  return (
    <div className="max-w-4xl mx-auto py-8 px-4">
      <div className="flex justify-between items-center mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Glassroom</h1>
        <div className="flex items-center gap-4">
          <span className={`px-3 py-1 rounded-full text-sm ${isConnected ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'}`}>
            {isConnected ? 'Connected' : 'Disconnected'}
          </span>
        </div>
      </div>

      <div className="mb-4">
        <label className="block text-sm font-medium text-gray-700 mb-1">Sprint ID</label>
        <div className="flex gap-2">
          <input
            type="text"
            value={sprintId}
            onChange={e => setSprintId(e.target.value)}
            className="flex-1 rounded-md border-gray-300 shadow-sm p-2 border"
            placeholder="Enter sprint ID"
          />
        </div>
      </div>

      {error && (
        <div className="mb-4 p-4 bg-red-50 border border-red-200 rounded-lg">
          <p className="text-red-800">Connection error: {error}</p>
        </div>
      )}

      <div className="bg-gray-900 rounded-lg p-4 h-[500px] overflow-y-auto font-mono text-sm">
        {events.length === 0 ? (
          <p className="text-gray-500">Waiting for events...</p>
        ) : (
          events.map((event, index) => (
            <div key={index} className="mb-2 pb-2 border-b border-gray-700">
              <span className="text-gray-400">[{new Date(event.timestamp).toLocaleTimeString()}]</span>
              <pre className="text-green-400 mt-1 overflow-x-auto">
                {JSON.stringify(event.data, null, 2)}
              </pre>
            </div>
          ))
        )}
        <div ref={eventsEndRef} />
      </div>

      <div className="mt-4 flex justify-between text-sm text-gray-500">
        <span>Total events: {events.length}</span>
        <button
          onClick={() => setEvents([])}
          className="text-indigo-600 hover:text-indigo-800"
        >
          Clear
        </button>
      </div>
    </div>
  )
}
