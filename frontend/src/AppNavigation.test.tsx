// The app's evidence-first navigation: primary destinations and their order,
// capability-gated links, the Company menu, the mobile menu, analytics and the
// sprint routes. The rest of the app's tests are in App.test.tsx; what they
// share is in test/appFixtures.tsx.
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import i18n from 'i18next'
import App from './App'
import { api, ApiError } from './services/api'
import { useAuthStore } from './store/useAuthStore'
import { useAppStore } from './store/useAppStore'
import { useAgentStore } from './store/agentStore'
import { queryClient } from './lib/queryClient'
import es from './i18n/es.json'
import { authenticate, installAppTestHooks, render } from './test/appFixtures'

installAppTestHooks()

describe('app navigation', () => {
  describe('evidence-first navigation', () => {
    it.each([
      ['en', 'Evidence', 'Review', 'Sprints', 'Open menu', 'Mobile navigation'],
      ['es', 'Evidencia', 'Revisión', 'Sprints', 'Abrir menú', 'Navegación móvil'],
    ])('orders primary destinations in %s on desktop and mobile', async (lang, evidence, review, sprints, openMenu, mobileNav) => {
      i18n.addResourceBundle('es', 'translation', es)
      await i18n.changeLanguage(lang)
      authenticate()
      render(<App />)
      await waitFor(() => expect(window.location.pathname).toBe('/evidence'))
      const nav = screen.getByRole('navigation', { name: lang === 'es' ? 'Navegación principal' : 'Main navigation' })
      expect(within(nav).getAllByRole('link').map(link => link.textContent)).toEqual([evidence, review, sprints])
      expect(within(nav).getAllByRole('link').filter(link => link.getAttribute('aria-current') === 'page')).toHaveLength(1)
      expect(screen.getByRole('button', { name: 'Example company' })).toHaveAttribute('aria-haspopup', 'menu')
      expect(screen.getByRole('link', { name: /^La Fabrique/ })).toHaveAttribute('href', '/evidence')
      expect(screen.getByText(lang === 'es' ? 'En línea' : 'Online')).toBeInTheDocument()
      fireEvent.click(screen.getByRole('button', { name: openMenu }))
      expect(within(screen.getByRole('navigation', { name: mobileNav })).getAllByRole('link').map(link => link.textContent)).toEqual([evidence, review, sprints])
    })

    it.each([
      [false, false], [true, false], [false, true], [true, true],
    ])('shows optional links only for reported capabilities (graph %s, memory %s)', async (agent_graph, memory) => {
      authenticate()
      vi.mocked(api.capabilities).mockResolvedValue({ agent_graph, memory, github: false, auth_required: true })
      render(<App />)
      const nav = screen.getByRole('navigation', { name: 'Main navigation' })
      await waitFor(() => expect(api.capabilities).toHaveBeenCalled())
      await waitFor(() => {
        expect(Boolean(within(nav).queryByRole('link', { name: 'Generate' }))).toBe(agent_graph)
        expect(Boolean(within(nav).queryByRole('link', { name: 'Memory' }))).toBe(memory)
      })
    })

    it('hides optional links while capabilities load or fail', async () => {
      authenticate()
      let reject!: (reason: Error) => void
      vi.mocked(api.capabilities).mockImplementation(() => new Promise((_resolve, rejectPromise) => { reject = rejectPromise }))
      render(<App />)
      const nav = screen.getByRole('navigation', { name: 'Main navigation' })
      expect(within(nav).queryByRole('link', { name: 'Generate' })).not.toBeInTheDocument()
      expect(within(nav).queryByRole('link', { name: 'Memory' })).not.toBeInTheDocument()
      await act(async () => reject(new ApiError(404, 'not available')))
      await waitFor(() => expect(queryClient.getQueryState(['capabilities'])?.status).toBe('error'))
      expect(within(nav).queryByRole('link', { name: 'Generate' })).not.toBeInTheDocument()
      expect(within(nav).queryByRole('link', { name: 'Memory' })).not.toBeInTheDocument()
    })

    it('hides previously enabled optional links if a capabilities refresh fails', async () => {
      authenticate()
      vi.mocked(api.capabilities).mockResolvedValue({ agent_graph: true, memory: true, github: false, auth_required: true })
      render(<App />)
      const nav = screen.getByRole('navigation', { name: 'Main navigation' })
      expect(await within(nav).findByRole('link', { name: 'Generate' })).toBeInTheDocument()
      expect(within(nav).getByRole('link', { name: 'Memory' })).toBeInTheDocument()
      vi.mocked(api.capabilities).mockRejectedValue(new ApiError(404, 'not available'))
      await act(async () => { await queryClient.invalidateQueries({ queryKey: ['capabilities'] }) })
      await waitFor(() => {
        expect(within(nav).queryByRole('link', { name: 'Generate' })).not.toBeInTheDocument()
        expect(within(nav).queryByRole('link', { name: 'Memory' })).not.toBeInTheDocument()
      })
    })

    it('keeps no-membership onboarding ahead of the landing redirect', async () => {
      authenticate()
      useAuthStore.setState({ memberships: [] })
      render(<App />)
      await waitFor(() => expect(window.location.pathname).toBe('/companies/new'))
    })

    it('opens quotas from Company and retains logout', async () => {
      authenticate()
      vi.spyOn(api, 'getQuotas').mockResolvedValue([{ id: 7, unit: 'Engineering', used: 80, limit: 100 }])
      render(<App />)
      expect(screen.queryByRole('menu')).not.toBeInTheDocument()
      const button = screen.getByRole('button', { name: 'Example company' })
      expect(button).toHaveAttribute('aria-expanded', 'false')
      fireEvent.click(button)
      expect(button).toHaveAttribute('aria-expanded', 'true')
      expect(screen.getByText('Example company')).toBeInTheDocument()
      expect(screen.getByRole('menuitem', { name: 'New company' })).toHaveAttribute('href', '/companies/new')
      expect(screen.getByRole('menuitem', { name: 'Agents' })).toHaveAttribute('href', '/agents')
      fireEvent.click(screen.getByRole('menuitem', { name: 'Quotas' }))
      expect(await screen.findByText('Engineering')).toBeInTheDocument()
      expect(screen.getByRole('heading', { name: 'Token Quota Usage' })).toBeInTheDocument()
      expect(window.location.pathname).toBe('/company/quotas')
      expect(screen.queryByRole('menu')).not.toBeInTheDocument()
      expect(screen.queryByText('No agent runs awaiting review')).not.toBeInTheDocument()
      fireEvent.click(screen.getByRole('button', { name: 'Example company' }))
      fireEvent.click(screen.getByRole('menuitem', { name: 'Log out' }))
      await waitFor(() => expect(window.location.pathname).toBe('/login'))
      expect(useAuthStore.getState().expiresAt).toBeNull()
    })

    it('closes the Company dropdown with Escape', () => {
      authenticate()
      render(<App />)
      const button = screen.getByRole('button', { name: 'Example company' })
      fireEvent.click(button)
      const item = screen.getByRole('menuitem', { name: 'Quotas' })
      item.focus()
      fireEvent.keyDown(item, { key: 'Escape' })
      expect(button).toHaveAttribute('aria-expanded', 'false')
      expect(button).toHaveFocus()
      expect(screen.queryByRole('menu')).not.toBeInTheDocument()
    })

    it('closes the Company dropdown on an outside click', () => {
      authenticate()
      render(<App />)
      fireEvent.click(screen.getByRole('button', { name: 'Example company' }))
      fireEvent.pointerDown(document.body)
      expect(screen.queryByRole('menu')).not.toBeInTheDocument()
    })

    it('falls back to a localized company ID when its name is unavailable', async () => {
      i18n.addResourceBundle('es', 'translation', es)
      await i18n.changeLanguage('es')
      authenticate()
      useAppStore.setState({ companyName: null })
      render(<App />)
      expect(screen.getByRole('button', { name: 'Empresa 1' })).toHaveAttribute('aria-haspopup', 'menu')
    })

    it('lists Company actions for multiple memberships and closes on outside click', async () => {
      authenticate()
      useAuthStore.setState({ memberships: [
        { company_id: 1, human_role: 'Owner' },
        { company_id: 2, human_role: 'Viewer' },
      ] })
      window.history.replaceState(null, '', '/evidence')
      render(<App />)
      fireEvent.click(screen.getByRole('button', { name: 'Example company' }))
      expect(within(screen.getByRole('menu')).getAllByRole('menuitem').map(item => item.textContent))
        .toEqual(['Switch company', 'New company', 'Agents', 'Quotas', 'Models', 'Project settings', 'Notifications', 'Routines', 'Procedures', 'Findings', 'Bench', 'Model catalog', 'Specifications', 'Documentation', 'Wiki', 'Security tests', 'Execution', 'MCP servers', 'Self-improvement', 'Members', 'Invitations', 'Account', 'Teams', 'Duties and access', 'Usage', 'Single sign-on', 'Chat approvals', 'Audit trail', 'Audit anchors', 'API tokens', 'GitHub', 'Log out'])
      fireEvent.pointerDown(document.body)
      expect(screen.queryByRole('menu')).not.toBeInTheDocument()
    })

    // Members (invitations: Manager and up), Teams, Duties and access, Usage and GitHub follow the company's effective role (a
    // team can raise a Viewer to Manager, never to Owner); the help desk
    // follows the platform's support admin flag, whatever the role.
    it.each([
      ['Owner', undefined, false, ['Members', 'Teams', 'Duties and access', 'Usage', 'Single sign-on', 'GitHub']],
      ['Manager', undefined, false, ['Members', 'Usage', 'GitHub']],
      ['Viewer', 'Manager', false, ['Members', 'Usage', 'GitHub']],
      ['Viewer', undefined, false, []],
      ['Viewer', undefined, true, ['Help desk']],
    ] as const)('offers the admin pages a %s (effective %s, support admin: %s) can use', (role, effective, supportAdmin, expected) => {
      authenticate()
      useAuthStore.setState({
        memberships: [{ company_id: 1, human_role: role, effective_role: effective }],
        user: { user_id: 1, email: 'person@example.invalid', full_name: 'Person', support_admin: supportAdmin },
      })
      render(<App />)
      fireEvent.click(screen.getByRole('button', { name: 'Example company' }))
      const admin = within(screen.getByRole('menu')).getAllByRole('menuitem')
        .filter(item => ['Members', 'Teams', 'Duties and access', 'Usage', 'Single sign-on', 'GitHub', 'Help desk'].includes(item.textContent ?? ''))
      expect(admin.map(item => [item.textContent, item.getAttribute('href')])).toEqual(
        expected.map(name => [name, { Members: '/company/members', Teams: '/company/teams', 'Duties and access': '/company/duties', Usage: '/company/usage', 'Single sign-on': '/company/sso', GitHub: '/company/github', 'Help desk': '/support' }[name]]),
      )
    })

    it('uses the company placeholder when its name is unavailable', () => {
      authenticate()
      useAppStore.setState({ companyName: null })
      render(<App />)
      expect(screen.getByRole('button', { name: 'Company 1' })).toHaveAttribute('aria-haspopup', 'menu')
    })

    it.each([
      ['A user', 'person@example.invalid', 'A user'],
      ['', 'person@example.invalid', 'person@example.invalid'],
    ])('uses the user name/email without a company (%s)', (full_name, email, label) => {
      authenticate()
      useAppStore.setState({ companyId: null, companyName: null })
      useAuthStore.setState({ loadingMe: true, user: { full_name, email, user_id: 1 } })
      render(<App />)
      expect(screen.getByRole('button', { name: label })).toHaveAttribute('aria-haspopup', 'menu')
    })

    it('closes the mobile menu on Escape and restores focus to its labelled toggle', async () => {
      i18n.addResourceBundle('es', 'translation', es)
      await i18n.changeLanguage('es')
      authenticate()
      window.history.replaceState(null, '', '/evidence')
      render(<App />)
      const toggle = screen.getByRole('button', { name: 'Abrir menú' })
      expect(toggle).toHaveAttribute('aria-controls', 'mobile-menu')
      expect(toggle).toHaveAttribute('aria-expanded', 'false')
      fireEvent.click(toggle)
      expect(toggle).toHaveAccessibleName('Cerrar menú')
      const mobile = screen.getByRole('navigation', { name: 'Navegación móvil' })
      const link = within(mobile).getByRole('link', { name: 'Revisión' })
      link.focus()
      fireEvent.keyDown(link, { key: 'Escape' })
      expect(toggle).toHaveAttribute('aria-expanded', 'false')
      expect(toggle).toHaveAccessibleName('Abrir menú')
      expect(toggle).toHaveFocus()
      expect(screen.queryByRole('navigation', { name: 'Navegación móvil' })).not.toBeInTheDocument()
    })

    it('shows on Review how many things wait for a person', async () => {
      vi.mocked(api.getInbox).mockResolvedValue({ blocked_tasks: [], proposals: [], pending_runs: 2, total: 3 })
      authenticate()
      useAppStore.setState({ companyId: 42, companyName: 'Example' })
      window.history.replaceState(null, '', '/evidence')
      render(<App />)
      const main = screen.getByRole('navigation', { name: 'Main navigation' })
      await waitFor(() => expect(within(main).getByRole('link', { name: /Review.*3 waiting for you/ })).toBeInTheDocument())
      expect(api.getInbox).toHaveBeenCalledWith(42)
    })

    it('closes the mobile menu after selecting a destination', async () => {
      authenticate()
      window.history.replaceState(null, '', '/evidence')
      render(<App />)
      fireEvent.click(screen.getByRole('button', { name: 'Open menu' }))
      const mobile = screen.getByRole('navigation', { name: 'Mobile navigation' })
      fireEvent.click(within(mobile).getByRole('link', { name: 'Review' }))
      await waitFor(() => expect(window.location.pathname).toBe('/governance'))
      expect(screen.getByRole('button', { name: 'Open menu' })).toHaveAttribute('aria-expanded', 'false')
    })

    it.each([
      ['/evidence', 'Evidence'],
      ['/evidence/1', 'Evidence'],
      ['/governance', 'Review'],
      ['/dashboard', 'Sprints'],
      ['/dashboard/analytics', 'Sprints'],
    ])('has exactly one active primary destination at %s', (path, label) => {
      authenticate()
      vi.spyOn(api, 'getAgentRun').mockRejectedValue(new ApiError(404, 'not available'))
      vi.spyOn(api, 'getAgentRunVerifications').mockResolvedValue({ verifications: [] } as never)
      vi.spyOn(api, 'getAgentRunApprovals').mockResolvedValue({ approvals: [] } as never)
      window.history.replaceState(null, '', path)
      render(<App />)
      const primary = screen.getByRole('navigation', { name: 'Main navigation' })
      const active = within(primary).getAllByRole('link').filter(link => link.getAttribute('aria-current') === 'page')
      expect(active).toHaveLength(1)
      expect(active[0]).toHaveTextContent(label)
      expect(document.querySelectorAll('[aria-current="page"]')).toHaveLength(1)
      fireEvent.click(screen.getByRole('button', { name: 'Open menu' }))
      const mobile = screen.getByRole('navigation', { name: 'Mobile navigation' })
      const mobileActive = within(mobile).getAllByRole('link').filter(link => link.getAttribute('aria-current') === 'page')
      expect(mobileActive).toHaveLength(1)
      expect(mobileActive[0]).toHaveTextContent(label)
    })

    it.each(['/dashboard/analytics', '/analytics', '/dashboard/analytics/', '/Dashboard/Analytics'])('nests analytics in the active sprint at %s', async path => {
      authenticate()
      useAppStore.getState().setActiveSprint(7)
      window.history.replaceState(null, '', path)
      vi.spyOn(api, 'getSprintBoard').mockResolvedValue({
        sprint: { id: 7, company_id: 1, sprint_name: 'Hardening', status: 'active', scope_size: 'M' }, columns: {}, total: 0,
      } as never)
      vi.spyOn(api, 'getAnalyticsSummary').mockResolvedValue({} as never)
      render(<App />)
      await waitFor(() => expect(window.location.pathname).toBe('/sprints/7/analytics'))
      const primary = screen.getByRole('navigation', { name: 'Main navigation' })
      expect(within(primary).getByRole('link', { name: 'Sprints' })).toHaveAttribute('aria-current', 'page')
      expect(within(primary).queryByRole('link', { name: 'Analytics' })).not.toBeInTheDocument()
      const tabs = await screen.findByRole('tablist', { name: 'Sprint views' })
      expect(within(tabs).getByRole('tab', { name: 'Analytics' })).toHaveAttribute('aria-selected', 'true')
      expect(within(tabs).getByRole('tab', { name: 'Board' })).toHaveAttribute('aria-selected', 'false')
    })

    describe('sprint routes', () => {
      const sprintBoard = {
        sprint: { id: 7, company_id: 1, sprint_name: 'Hardening', status: 'active', scope_size: 'M', goal: '', start_date: '2026-10-01', item_limit: 8 },
        columns: { todo: [{
          id: 2, sprint_id: 7, title: 'Encrypt credentials', description: 'Deliverable', status: 'todo', priority: 'high',
          assignee: 'Developer', comments: [], created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z',
        }] },
        total: 1,
      }

      beforeEach(() => {
        authenticate()
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 404 }))
        vi.spyOn(api, 'getSprintBoard').mockResolvedValue(sprintBoard as never)
        vi.spyOn(api, 'getAnalyticsSummary').mockResolvedValue({} as never)
        vi.spyOn(api, 'getQuotas').mockResolvedValue([])
        vi.spyOn(api, 'getAgents').mockResolvedValue([] as never)
        vi.spyOn(useAgentStore.getState(), 'connect').mockImplementation(() => {})
        vi.spyOn(useAgentStore.getState(), 'disconnect').mockImplementation(() => {})
      })

      it.each([
        ['/dashboard', 7, '/sprints/7'],
        ['/dashboard', null, '/sprints'],
        ['/dashboard/analytics', 7, '/sprints/7/analytics'],
        ['/dashboard/analytics', null, '/sprints'],
        ['/analytics', 7, '/sprints/7/analytics'],
        ['/analytics', null, '/sprints'],
        ['/workspace/7', null, '/sprints/7/live'],
        ['/workspace/7/live', null, '/sprints/7/live'],
        ['/sprint-ops/7', null, '/sprints/7/board'],
        ['/workspace/7/task/2', null, '/sprints/7/tasks/2'],
      ])('redirects %s (active sprint %s) to %s', async (from, active, to) => {
        useAppStore.getState().setActiveSprint(active)
        window.history.replaceState(null, '', from)
        render(<App />)
        await waitFor(() => expect(window.location.pathname).toBe(to))
        const primary = screen.getByRole('navigation', { name: 'Main navigation' })
        expect(within(primary).getByRole('link', { name: 'Sprints' })).toHaveAttribute('aria-current', 'page')
        expect(screen.queryByText('404')).not.toBeInTheDocument()
      })

      it('keeps /task/:taskId working for the active sprint, with a breadcrumb', async () => {
        useAppStore.getState().setActiveSprint(7)
        window.history.replaceState(null, '', '/task/2')
        render(<App />)
        expect(await screen.findByRole('heading', { level: 1, name: 'Encrypt credentials' })).toBeInTheDocument()
        const breadcrumb = screen.getByRole('navigation', { name: 'Breadcrumb' })
        expect(within(breadcrumb).getAllByRole('listitem').map(item => item.textContent)).toEqual(['Sprints', 'Hardening', 'Encrypt credentials'])
      })

      it('links Sprints in the primary navigation to the sprint list', async () => {
        window.history.replaceState(null, '', '/evidence')
        render(<App />)
        const primary = screen.getByRole('navigation', { name: 'Main navigation' })
        fireEvent.click(within(primary).getByRole('link', { name: 'Sprints' }))
        await waitFor(() => expect(window.location.pathname).toBe('/sprints'))
        expect(await screen.findByRole('heading', { level: 1, name: 'Sprints' })).toBeInTheDocument()
      })

      it.each([
        ['/sprints', 'Sprints', false],
        ['/sprints/7/board', 'Sprints', true],
        ['/sprints/7/tasks/2', 'Sprints', true],
        ['/evidence/1', 'Evidence', true],
        ['/agents', null, false],
        ['/company/quotas', null, false],
        ['/companies/new', null, false],
      ])('marks exactly one current element at %s', async (path, primaryLabel, breadcrumb) => {
        vi.spyOn(api, 'getAgentRun').mockResolvedValue({
          id: 1, company_id: 1, external_ref: 'run-1', version: 1, producer: 'github', status: 'unverifiable', claims: {},
        } as never)
        vi.spyOn(api, 'getAgentRunVerifications').mockResolvedValue({ verifications: [] } as never)
        vi.spyOn(api, 'getAgentRunApprovals').mockResolvedValue({ approvals: [] } as never)
        window.history.replaceState(null, '', path)
        render(<App />)
        if (breadcrumb) expect(await screen.findByRole('navigation', { name: 'Breadcrumb' })).toBeInTheDocument()
        const current = Array.from(document.querySelectorAll('[aria-current="page"]'))
        expect(current).toHaveLength(1)
        const primary = screen.getByRole('navigation', { name: 'Main navigation' })
        const company = screen.getByRole('button', { name: 'Example company' })
        if (primaryLabel) {
          expect(within(primary).getByRole('link', { name: primaryLabel })).toBe(current[0])
          expect(company).not.toHaveAttribute('aria-current')
        } else {
          expect(company).toBe(current[0])
          expect(company.className).toContain('nav-link-current')
          expect(within(primary).queryAllByRole('link').filter(link => link.hasAttribute('aria-current'))).toHaveLength(0)
        }
      })
    })

    it('removes the duplicate Glassroom route', async () => {
      authenticate()
      window.history.replaceState(null, '', '/glassroom')
      render(<App />)
      expect(await screen.findByText('404')).toBeInTheDocument()
      expect(screen.queryByLabelText('Sprint ID')).not.toBeInTheDocument()
    })
  })
})
