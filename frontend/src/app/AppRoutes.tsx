import { Routes, Route, Navigate, useParams } from 'react-router-dom'
import { useAppStore } from '../store/useAppStore'
import ProtectedRoute from '../components/ProtectedRoute'
import AgentSettings from '../pages/AgentSettings'
import Sprints from '../pages/Sprints'
import CompanyNew from '../pages/CompanyNew'
import CompanySelect from '../pages/CompanySelect'
import Login from '../pages/Login'
import Register from '../pages/Register'
import { SprintWorkspace, sprintPath, type SprintTab } from '../pages/SprintWorkspace'
import { GovernancePanel } from '../components/GovernancePanel'
import { QuotaPanel } from '../components/QuotaPanel'
import MemorySearch from '../pages/MemorySearch'
import Evidence from '../pages/Evidence'
import EvidenceDetail from '../pages/EvidenceDetail'
import Generate from '../pages/Generate'
import NotFound from '../pages/NotFound'
import TaskDetails from '../pages/TaskDetails'
import CompanyMembers from '../pages/CompanyMembers'
import Invitations from '../pages/Invitations'
import Account from '../pages/Account'
import CompanySso from '../pages/CompanySso'
import ChatApprovals from '../pages/ChatApprovals'
import CompanyTeams from '../pages/CompanyTeams'
import CompanyDuties from '../pages/CompanyDuties'
import CompanyFindingDetail from '../pages/CompanyFindingDetail'
import CompanyFindings from '../pages/CompanyFindings'
import Usage from '../pages/Usage'
import AuditAnchors from '../pages/AuditAnchors'
import SecurityTests from '../pages/SecurityTests'
import ExecutionSettings from '../pages/ExecutionSettings'
import McpServers from '../pages/McpServers'
import SelfImprovement from '../pages/SelfImprovement'
import SsoComplete from '../pages/SsoComplete'
import GitHubIntegration from '../pages/GitHubIntegration'
import HelpDesk from '../pages/HelpDesk'
import Lineage from '../pages/Lineage'
import Procedures from '../pages/Procedures'
import Documentation from '../pages/Documentation'
import Wiki from '../pages/Wiki'
import Models from '../pages/Models'
import Specifications from '../pages/Specifications'
import SpecificationDetail from '../pages/SpecificationDetail'
import ArchitectureDetail from '../pages/ArchitectureDetail'
import Bench from '../pages/Bench'

// ── Legacy route redirects ────────────────────────────────────────────────────

function ActiveSprintRedirect({ tab }: { tab?: SprintTab }) {
  const activeSprintId = useAppStore(state => state.activeSprintId)
  return <Navigate to={activeSprintId ? sprintPath(activeSprintId, tab) : '/sprints'} replace />
}

function SprintParamRedirect({ tab }: { tab: SprintTab }) {
  const { id = '' } = useParams()
  return <Navigate to={sprintPath(encodeURIComponent(id), tab)} replace />
}

function LegacyTaskRedirect() {
  const { sprintId = '', taskId = '' } = useParams()
  return <Navigate to={`/sprints/${encodeURIComponent(sprintId)}/tasks/${encodeURIComponent(taskId)}`} replace />
}

// ── Routes ────────────────────────────────────────────────────────────────────

/** Every route of the application; all but sign-in sit behind ProtectedRoute. */
export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/register" element={<Register />} />
      <Route path="/login/sso" element={<SsoComplete />} />
      <Route element={<ProtectedRoute />}>
        <Route path="/" element={<Navigate to="/evidence" replace />} />
        <Route path="/sprints" element={<Sprints />} />
        <Route path="/sprints/:id" element={<SprintWorkspace />} />
        <Route path="/sprints/:id/:tab" element={<SprintWorkspace />} />
        <Route path="/sprints/:sprintId/tasks/:taskId" element={<TaskDetails />} />
        <Route path="/dashboard" element={<ActiveSprintRedirect />} />
        <Route path="/dashboard/analytics" element={<ActiveSprintRedirect tab="analytics" />} />
        <Route path="/analytics" element={<ActiveSprintRedirect tab="analytics" />} />
        <Route path="/workspace/:id" element={<SprintParamRedirect tab="live" />} />
        <Route path="/workspace/:id/live" element={<SprintParamRedirect tab="live" />} />
        <Route path="/workspace/:sprintId/task/:taskId" element={<LegacyTaskRedirect />} />
        <Route path="/sprint-ops/:id" element={<SprintParamRedirect tab="board" />} />
        <Route path="/companies/new" element={<CompanyNew />} />
        <Route path="/companies/select" element={<CompanySelect />} />
        <Route path="/company/quotas" element={<div className="page-container"><QuotaPanel /></div>} />
        <Route path="/company/members" element={<CompanyMembers />} />
        <Route path="/invitations" element={<Invitations />} />
        <Route path="/account" element={<Account />} />
        <Route path="/company/sso" element={<CompanySso />} />
        <Route path="/company/chat" element={<ChatApprovals />} />
        <Route path="/company/teams" element={<CompanyTeams />} />
        <Route path="/company/duties" element={<CompanyDuties />} />
        <Route path="/company/security-tests" element={<SecurityTests />} />
        <Route path="/company/execution" element={<ExecutionSettings />} />
        <Route path="/company/mcp" element={<McpServers />} />
        <Route path="/company/self-improvement" element={<SelfImprovement />} />
        <Route path="/company/usage" element={<Usage />} />
        <Route path="/company/audit-anchors" element={<AuditAnchors />} />
        <Route path="/company/procedures" element={<Procedures />} />
        <Route path="/company/findings" element={<CompanyFindings />} />
        <Route path="/company/findings/:id" element={<CompanyFindingDetail />} />
        <Route path="/bench" element={<Bench />} />
        <Route path="/company/wiki" element={<Wiki />} />
        <Route path="/company/models" element={<Models />} />
        <Route path="/company/specifications" element={<Specifications />} />
        <Route path="/company/specifications/:id" element={<SpecificationDetail />} />
        <Route path="/company/specifications/:specId/architecture/new" element={<ArchitectureDetail />} />
        <Route path="/company/specifications/:specId/architectures/:id" element={<ArchitectureDetail />} />
        <Route path="/company/documentation" element={<Documentation />} />
        <Route path="/company/github" element={<GitHubIntegration />} />
        <Route path="/support" element={<HelpDesk />} />
        <Route path="/agents" element={<AgentSettings />} />
        <Route
          path="/governance"
          element={<div className="page-container"><GovernancePanel /></div>}
        />
        <Route path="/evidence" element={<Evidence />} />
        <Route path="/evidence/:runId" element={<EvidenceDetail />} />
        <Route path="/lineage" element={<Lineage />} />
        <Route path="/memory" element={<MemorySearch />} />
        <Route path="/generate" element={<Generate />} />
        <Route path="/task/:taskId" element={<TaskDetails />} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  )
}
