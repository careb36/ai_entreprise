import { useState, useEffect } from 'react'
import { useParams } from 'react-router-dom'

export default function TaskDetails() {
  const { id } = useParams()
  const [task, setTask] = useState<any>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(false)
    setTask({
      id: id,
      subject: 'Sample Task',
      description: 'Task description from OpenProject',
      status: 'open',
      assignee: 'Unassigned'
    })
  }, [id])

  if (loading) return <div className="p-8">Loading...</div>

  return (
    <div className="max-w-4xl mx-auto py-8 px-4">
      <h1 className="text-2xl font-bold text-gray-900 mb-6">Task #{id}</h1>
      
      <div className="bg-white shadow rounded-lg p-6">
        <div className="mb-4">
          <label className="block text-sm font-medium text-gray-500">Subject</label>
          <p className="text-lg text-gray-900">{task?.subject}</p>
        </div>
        
        <div className="mb-4">
          <label className="block text-sm font-medium text-gray-500">Description</label>
          <p className="text-gray-700">{task?.description}</p>
        </div>
        
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium text-gray-500">Status</label>
            <span className="px-2 py-1 bg-yellow-100 text-yellow-800 rounded text-sm">
              {task?.status}
            </span>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-500">Assignee</label>
            <p className="text-gray-700">{task?.assignee}</p>
          </div>
        </div>
      </div>
    </div>
  )
}
