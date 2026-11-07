import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ArchitectureEditor } from './ArchitectureEditor'
import { APPROVED } from '../test/specificationFixtures'

// Label queries, not role queries: a role query walks every element of the
// form for its computed role and accessible name, and this form grows to a
// few dozen fields; on a loaded CI runner the role queries alone took the
// test past its timeout.
function fill(scope: HTMLElement, name: string, value: string) {
  fireEvent.change(within(scope).getByLabelText(name), { target: { value } })
}

describe('ArchitectureEditor', () => {
  it('builds the body from the title, the modules with a dependency and coverage, an interface, a decision and a convention', () => {
    const onSave = vi.fn()
    render(<ArchitectureEditor requirements={APPROVED.requirements} onSave={onSave} onCancel={vi.fn()} saving={false} error={null} />)
    const form = screen.getByRole('form', { name: 'Architecture editor' })
    expect(form).toHaveTextContent('Ids (MOD-1, IF-1, ADR-1) are assigned when you save')
    const save = within(form).getByRole('button', { name: 'Save' })
    expect(save).toBeDisabled()

    fill(form, 'Title', ' Login design ')
    fill(form, 'Overview', 'A session service.')
    fill(form, 'Name of module 1', 'Sessions')
    fill(form, 'Path of module 1', 'src/sessions')
    fill(form, 'Responsibility of module 1', 'Issues tokens.')
    expect(save).toBeEnabled()
    // Nothing to depend on until a second module is named.
    expect(within(form).queryByRole('checkbox', { name: /depends on/ })).not.toBeInTheDocument()
    fireEvent.click(within(form).getByRole('checkbox', { name: 'Module 1 covers REQ-1' }))

    fireEvent.click(within(form).getByRole('button', { name: 'Add a module' }))
    expect(save).toBeDisabled()
    fill(form, 'Name of module 2', 'Web')
    fill(form, 'Path of module 2', 'src/web')
    fireEvent.click(within(form).getByRole('checkbox', { name: 'Module 2 depends on Sessions' }))
    expect(within(form).queryByRole('checkbox', { name: 'Module 2 depends on Web' })).not.toBeInTheDocument()

    fireEvent.click(within(form).getByRole('button', { name: 'Add an interface' }))
    expect(save).toBeDisabled()
    fill(form, 'Name of interface 1', 'Session API')
    fireEvent.change(within(form).getByRole('combobox', { name: 'Kind of interface 1' }), { target: { value: 'http' } })
    fireEvent.change(within(form).getByRole('combobox', { name: 'Module providing interface 1' }), { target: { value: 'Sessions' } })
    fill(form, 'Contract of interface 1', 'POST /login\nDELETE /login')
    // The provider is not offered as its own consumer.
    expect(within(form).queryByRole('checkbox', { name: 'Interface 1 consumed by Sessions' })).not.toBeInTheDocument()
    fireEvent.click(within(form).getByRole('checkbox', { name: 'Interface 1 consumed by Web' }))
    expect(save).toBeEnabled()

    fireEvent.click(within(form).getByRole('button', { name: 'Add a decision' }))
    fill(form, 'Title of decision 1', 'Stateless tokens')
    fill(form, 'Context of decision 1', 'Several nodes.')
    fill(form, 'Decision of decision 1', 'Signed JWTs.')
    fill(form, 'Consequences of decision 1', 'No session store.')
    fireEvent.click(within(form).getByRole('button', { name: 'Add a convention' }))
    fill(form, 'Convention 1', 'Tests beside the code')
    fireEvent.click(within(form).getByRole('button', { name: 'Add a convention' }))
    fireEvent.click(within(form).getByRole('button', { name: 'Remove convention 2' }))

    fireEvent.click(save)
    expect(onSave).toHaveBeenCalledWith({
      title: 'Login design', overview: 'A session service.',
      modules: [
        { name: 'Sessions', responsibility: 'Issues tokens.', path: 'src/sessions', depends_on: [], covers: ['REQ-1'] },
        { name: 'Web', responsibility: '', path: 'src/web', depends_on: ['Sessions'], covers: [] },
      ],
      interfaces: [{ name: 'Session API', kind: 'http', module: 'Sessions', contract: 'POST /login\nDELETE /login', consumers: ['Web'] }],
      decisions: [{ title: 'Stateless tokens', context: 'Several nodes.', decision: 'Signed JWTs.', consequences: 'No session store.' }],
      conventions: ['Tests beside the code'],
    })
  })

  it('starts from the given body, drops a removed module from the dependencies, and cancels', () => {
    const onSave = vi.fn()
    const onCancel = vi.fn()
    render(<ArchitectureEditor requirements={[]} onCancel={onCancel} onSave={onSave} saving={false} error="Could not save it"
      initial={{
        title: 'Login design', overview: '',
        modules: [
          { name: 'Sessions', responsibility: '', path: 'src/sessions', depends_on: [], covers: [] },
          { name: 'Web', responsibility: '', path: 'src/web', depends_on: ['Sessions'], covers: [] },
          { name: 'Mail', responsibility: '', path: 'src/mail', depends_on: ['Web'], covers: [] },
        ],
        interfaces: [{ name: 'Session API', kind: 'http', module: 'Sessions', contract: '', consumers: ['Web', 'Mail'] }],
        decisions: [], conventions: [],
      }} />)
    const form = screen.getByRole('form', { name: 'Architecture editor' })
    expect(within(form).getByRole('alert')).toHaveTextContent('Could not save it')
    // No requirements to cover: each module says so.
    expect(within(form).getAllByText('Nothing to pick from yet.')).toHaveLength(3)
    fireEvent.click(within(form).getByRole('button', { name: 'Remove module 2' }))
    fireEvent.click(within(form).getByRole('button', { name: 'Remove interface 1' }))
    fireEvent.click(within(form).getByRole('button', { name: 'Save' }))
    expect(onSave).toHaveBeenCalledWith({
      title: 'Login design', overview: '',
      modules: [
        { name: 'Sessions', responsibility: '', path: 'src/sessions', depends_on: [], covers: [] },
        { name: 'Mail', responsibility: '', path: 'src/mail', depends_on: [], covers: [] },
      ],
      interfaces: [], decisions: [], conventions: [],
    })
    fireEvent.click(within(form).getByRole('button', { name: 'Cancel' }))
    expect(onCancel).toHaveBeenCalledOnce()
  })
})
