import React, { Suspense } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { SidebarNav } from './components/navigation/SidebarNav'

// Placeholder views for standard application routes
const SprintsView = () => (
  <div data-testid="view-sprints" className="p-6">
    <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-100">Sprints & Tasks</h1>
    <p className="mt-2 text-sm text-slate-600 dark:text-slate-400">
      Manage active development sprints and evidence-driven tasks.
    </p>
  </div>
)

const BoardView = () => (
  <div data-testid="view-board" className="p-6">
    <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-100">Sprint Board</h1>
    <p className="mt-2 text-sm text-slate-600 dark:text-slate-400">
      Kanban visualization for agent and developer task workflow.
    </p>
  </div>
)

const GenerateView = () => (
  <div data-testid="view-generate" className="p-6">
    <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-100">Agent Graph Topology</h1>
    <p className="mt-2 text-sm text-slate-600 dark:text-slate-400">
      LangGraph execution DAG and validation gate topology editor.
    </p>
  </div>
)

const MemoryView = () => (
  <div data-testid="view-memory" className="p-6">
    <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-100">Orchestration & Memory</h1>
    <p className="mt-2 text-sm text-slate-600 dark:text-slate-400">
      Vector memory stores, RAG contexts, and episodic agent traces.
    </p>
  </div>
)

const EvidenceView = () => (
  <div data-testid="view-evidence" className="p-6">
    <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-100">Audit & Evidence</h1>
    <p className="mt-2 text-sm text-slate-600 dark:text-slate-400">
      Cryptographic verification logs, test execution proofs, and compliance gates.
    </p>
  </div>
)

const AuditView = () => (
  <div data-testid="view-audit" className="p-6">
    <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-100">Audit Trail</h1>
    <p className="mt-2 text-sm text-slate-600 dark:text-slate-400">
      Append-only audit trail and claims-vs-observations records.
    </p>
  </div>
)

const CompanyView = () => (
  <div data-testid="view-company" className="p-6">
    <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-100">Company & Governance</h1>
    <p className="mt-2 text-sm text-slate-600 dark:text-slate-400">
      Enterprise organization governance and compliance policies.
    </p>
  </div>
)

const AgentsView = () => (
  <div data-testid="view-agents" className="p-6">
    <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-100">Configured Agents</h1>
    <p className="mt-2 text-sm text-slate-600 dark:text-slate-400">
      Agent personas, prompt configurations, and tool permissions.
    </p>
  </div>
)

const SupportView = () => (
  <div data-testid="view-support" className="p-6">
    <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-100">Support & Documentation</h1>
    <p className="mt-2 text-sm text-slate-600 dark:text-slate-400">
      System documentation, API specifications, and troubleshooting guides.
    </p>
  </div>
)

export const AppContent: React.FC = () => {
  return (
    <div className="flex h-screen w-screen overflow-hidden bg-slate-50 dark:bg-slate-950 font-sans">
      <SidebarNav alertCount={2} />
      <main className="flex-1 overflow-y-auto focus:outline-none">
        <Suspense
          fallback={
            <div className="flex items-center justify-center h-full text-slate-400">
              Loading workspace...
            </div>
          }
        >
          <Routes>
            <Route path="/" element={<Navigate to="/sprints" replace />} />
            <Route path="/sprints" element={<SprintsView />} />
            <Route path="/board" element={<BoardView />} />
            <Route path="/generate" element={<GenerateView />} />
            <Route path="/memory" element={<MemoryView />} />
            <Route path="/evidence" element={<EvidenceView />} />
            <Route path="/audit" element={<AuditView />} />
            <Route path="/company" element={<CompanyView />} />
            <Route path="/agents" element={<AgentsView />} />
            <Route path="/support" element={<SupportView />} />
            <Route path="*" element={<Navigate to="/sprints" replace />} />
          </Routes>
        </Suspense>
      </main>
    </div>
  )
}

export const App: React.FC = () => {
  return (
    <BrowserRouter>
      <AppContent />
    </BrowserRouter>
  )
}

export default App
