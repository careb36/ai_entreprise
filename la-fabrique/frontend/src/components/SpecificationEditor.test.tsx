import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { SpecificationEditor } from './SpecificationEditor'

function fill(scope: HTMLElement, name: string, value: string) {
  fireEvent.change(within(scope).getByRole('textbox', { name }), { target: { value } })
}

describe('SpecificationEditor', () => {
  it('builds the body from the title, requirements with their criteria, quality attributes and constraints', () => {
    const onSave = vi.fn()
    render(<SpecificationEditor onSave={onSave} onCancel={vi.fn()} saving={false} error={null} />)
    const form = screen.getByRole('form', { name: 'Specification editor' })
    expect(form).toHaveTextContent('Ids (REQ-1, REQ-1.1, NFR-1) are assigned when you save.')
    const save = within(form).getByRole('button', { name: 'Save' })
    expect(save).toBeDisabled()

    fill(form, 'Title', ' Checkout ')
    fill(form, 'Summary', 'Buy things.')
    fill(form, 'Title of requirement 1', 'Cart')
    fill(form, 'Description of requirement 1', 'Add items.')
    fill(form, 'Given of criterion 1.1', 'a cart')
    fill(form, 'When of criterion 1.1', 'an item is added')
    fill(form, 'Then of criterion 1.1', 'it is listed')
    expect(save).toBeEnabled()

    // A second requirement with two criteria: the second row is added.
    fireEvent.click(within(form).getByRole('button', { name: 'Add a requirement' }))
    expect(save).toBeDisabled()
    fill(form, 'Title of requirement 2', 'Payment')
    fill(form, 'Given of criterion 2.1', 'a cart with items')
    fill(form, 'When of criterion 2.1', 'the person pays')
    fill(form, 'Then of criterion 2.1', 'an order exists')
    fireEvent.click(within(form).getAllByRole('button', { name: 'Add a criterion' })[1])
    fill(form, 'Given of criterion 2.2', 'a declined card')
    fill(form, 'When of criterion 2.2', 'the person pays')
    fill(form, 'Then of criterion 2.2', 'no order exists')

    fireEvent.click(within(form).getByRole('button', { name: 'Add a quality attribute' }))
    fill(form, 'Attribute 1', 'Latency')
    fill(form, 'Target of attribute 1', 'under 200 ms')
    fireEvent.click(within(form).getByRole('button', { name: 'Add a constraint' }))
    fill(form, 'Constraint 1', 'Postgres only')
    fireEvent.click(within(form).getByRole('button', { name: 'Add a constraint' }))
    fireEvent.click(within(form).getByRole('button', { name: 'Remove constraint 2' }))

    fireEvent.click(save)
    expect(onSave).toHaveBeenCalledWith({
      title: 'Checkout', summary: 'Buy things.',
      requirements: [
        { title: 'Cart', description: 'Add items.', criteria: [{ given: 'a cart', when: 'an item is added', then: 'it is listed' }] },
        { title: 'Payment', description: '', criteria: [
          { given: 'a cart with items', when: 'the person pays', then: 'an order exists' },
          { given: 'a declined card', when: 'the person pays', then: 'no order exists' },
        ] },
      ],
      quality: [{ attribute: 'Latency', target: 'under 200 ms' }],
      constraints: ['Postgres only'],
    })
  })

  it('starts from the given body, removes a requirement and a criterion, and cancels', () => {
    const onSave = vi.fn()
    const onCancel = vi.fn()
    render(<SpecificationEditor onCancel={onCancel} onSave={onSave} saving={false} error="Could not save it"
      initial={{
        title: 'Checkout', summary: '',
        requirements: [
          { title: 'Cart', description: '', criteria: [{ given: 'a', when: 'b', then: 'c' }, { given: 'd', when: 'e', then: 'f' }] },
          { title: 'Payment', description: '', criteria: [{ given: 'g', when: 'h', then: 'i' }] },
        ],
        quality: [], constraints: [],
      }} />)
    const form = screen.getByRole('form', { name: 'Specification editor' })
    expect(within(form).getByRole('alert')).toHaveTextContent('Could not save it')
    fireEvent.click(within(form).getByRole('button', { name: 'Remove criterion 1.2' }))
    fireEvent.click(within(form).getByRole('button', { name: 'Remove requirement 2' }))
    // The last requirement and its last criterion cannot be removed.
    expect(within(form).queryByRole('button', { name: /Remove requirement/ })).not.toBeInTheDocument()
    expect(within(form).queryByRole('button', { name: /Remove criterion/ })).not.toBeInTheDocument()
    fireEvent.click(within(form).getByRole('button', { name: 'Save' }))
    expect(onSave).toHaveBeenCalledWith({
      title: 'Checkout', summary: '',
      requirements: [{ title: 'Cart', description: '', criteria: [{ given: 'a', when: 'b', then: 'c' }] }],
      quality: [], constraints: [],
    })
    fireEvent.click(within(form).getByRole('button', { name: 'Cancel' }))
    expect(onCancel).toHaveBeenCalledOnce()
  })
})
