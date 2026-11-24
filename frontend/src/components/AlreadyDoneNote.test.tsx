import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { AlreadyDoneNote } from './AlreadyDoneNote'
import type { AlreadyDoneObserved, AlreadyDonePart } from '../services/api'
import en from '../i18n/en.json'
import es from '../i18n/es.json'

const claim: AlreadyDonePart[] = [
  { part: 'the --greet option', evidence: 'src/cli.py defines it', paths: ['src/cli.py'] },
  { part: 'its test', evidence: '', paths: ['tests/test_cli.py'] },
]

const observed: AlreadyDoneObserved = {
  files_read: [{ path: 'src/cli.py', sha256: 'abcdef0123456789abcdef', bytes: 40, tool: 'read', round: 1 }],
  searches: [],
  claimed_paths: [
    { path: 'src/cli.py', read: true, files: [{ path: 'src/cli.py', sha256: 'abcdef0123456789abcdef' }] },
    { path: 'tests/test_cli.py', read: false, files: [] },
  ],
}

describe('the PO says part of the objective is already done', () => {
  it('shows the claim apart from the files the platform read, and flags a path not read', () => {
    render(<AlreadyDoneNote claim={claim} observed={observed} />)
    const note = screen.getByRole('region', { name: 'The PO says part of this objective is already done' })
    expect(note).toHaveTextContent("The PO's claim (its words, not verified):")
    expect(note).toHaveTextContent('the --greet option')
    expect(note).toHaveTextContent('— src/cli.py defines it')
    expect(note).toHaveTextContent('Read by the platform in this planning turn (1 file, with the sha256 the daemon reported):')
    expect(within(note).getByTitle('abcdef0123456789abcdef')).toHaveTextContent('sha256 abcdef012345')

    const notRead = within(note).getAllByText('not read')
    expect(notRead).toHaveLength(1)
    expect(notRead[0].closest('code')).toHaveTextContent('tests/test_cli.py')
  })

  it('shows nothing without a claim', () => {
    const { container } = render(<AlreadyDoneNote claim={null} observed={observed} />)
    expect(container).toBeEmptyDOMElement()
    render(<AlreadyDoneNote claim={[]} observed={null} />)
    expect(screen.queryByRole('region')).not.toBeInTheDocument()
  })

  it('flags every claimed path when the platform observed no read', () => {
    render(<AlreadyDoneNote claim={claim} observed={null} />)
    expect(screen.getAllByText('not read')).toHaveLength(2)
    expect(screen.getByRole('region')).toHaveTextContent('(0 files,')
  })

  it('has its strings in English and Spanish', () => {
    expect(Object.keys(es.alreadyDone).sort()).toEqual(Object.keys(en.alreadyDone).sort())
  })
})
