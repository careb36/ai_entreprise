import { fireEvent, render, screen } from '@testing-library/react'
import i18n from 'i18next'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { PublishBranch } from './PublishBranch'
import { ApiError } from '../services/api'
import { type RunPublication, usePublishRun, useRunPublications } from '../services/runReviews'

vi.mock('../services/runReviews', () => ({
  usePublishRun: vi.fn(),
  useRunPublications: vi.fn(),
}))

const publish = vi.fn()

const PULL_REQUEST: RunPublication = {
  id: 1, agent_run_id: 7, provider: 'github', branch: 'fabrique/task-7', base: 'main',
  commit: 'abcdef0123456789', url: 'https://github.com/ana/app/pull/12', how: 'gh',
  published_by: 'Ana', created_at: '', updated: false,
}

function setup({ publications = [] as RunPublication[], mutation = {} as Record<string, unknown>, canPublish = true } = {}) {
  vi.mocked(useRunPublications).mockReturnValue({ data: { publications } } as never)
  vi.mocked(usePublishRun).mockReturnValue({ mutate: publish, isPending: false, isError: false, ...mutation } as never)
  return render(<PublishBranch companyId={42} runId={7} branch="fabrique/task-7" canPublish={canPublish} />)
}

describe('publishing a run branch', () => {
  beforeEach(() => vi.clearAllMocks())

  it('lets a Manager publish the branch', () => {
    setup()
    expect(screen.getByText(i18n.t('review.publish.hint', { branch: 'fabrique/task-7' }))).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: i18n.t('review.publish.button') }))
    expect(publish).toHaveBeenCalled()
  })

  it('links the request it opened, for everyone', () => {
    setup({ publications: [PULL_REQUEST], canPublish: false })
    const link = screen.getByRole('link', { name: i18n.t('review.publish.how.gh') })
    expect(link).toHaveAttribute('href', 'https://github.com/ana/app/pull/12')
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
    expect(screen.getByText(i18n.t('review.publish.pushed', { name: 'Ana', commit: 'abcdef0', branch: 'fabrique/task-7', base: 'main' }))).toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('says when an open request had its description updated', () => {
    setup({ publications: [{ ...PULL_REQUEST, updated: true }] })
    expect(screen.getByText(i18n.t('review.publish.updated'))).toBeInTheDocument()
  })

  it('says a remote without a web page was still pushed', () => {
    setup({ publications: [{ ...PULL_REQUEST, url: null, how: 'compare' }] })
    expect(screen.getByText(i18n.t('review.publish.noUrl'))).toBeInTheDocument()
    expect(screen.getByRole('button', { name: i18n.t('review.publish.again') })).toBeInTheDocument()
  })

  it('shows nothing to a Viewer when it was never published', () => {
    const { container } = setup({ canPublish: false })
    expect(container).toBeEmptyDOMElement()
  })

  it('says why the daemon refused', () => {
    setup({ mutation: { isError: true, error: new ApiError(409, 'the repository has no origin to push to', 'the repository has no origin to push to') } })
    expect(screen.getByRole('alert')).toHaveTextContent('no origin')
  })
})
