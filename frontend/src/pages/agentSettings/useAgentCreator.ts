import { useEffect, useState } from 'react'
import type { ModelCatalogEntry } from '../../services/api'
import { defaultDraft, type AgentDraft } from './drafts'
import { AUTO_RECOMMENDED_MODEL_IDS, recommendModelForRole } from './recommendation'

/** The new-agent creator: its draft, whether it is open, and bringing it into view. */
export function useAgentCreator(models: ModelCatalogEntry[]) {
  const [newAgent, setNewAgent] = useState<AgentDraft>(defaultDraft())
  const [creatorOpen, setCreatorOpen] = useState(false)
  // Bumped to bring the creator into view once it has rendered; an effect
  // instead of a timer, so nothing runs after the page unmounts.
  const [creatorScrollRequest, setCreatorScrollRequest] = useState(0)
  useEffect(() => {
    if (creatorScrollRequest === 0) return
    document.getElementById('agent-creator')?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
  }, [creatorScrollRequest])

  // Auto-recommend model on creator role/seniority change.
  useEffect(() => {
    if (!models.length) return
    setNewAgent(prev => {
      const recommendation = recommendModelForRole(models, prev.role, prev.seniority)
      if (!recommendation.primary) return prev
      if (AUTO_RECOMMENDED_MODEL_IDS.has(prev.model)) {
        return { ...prev, model: recommendation.primary.id }
      }
      return prev
    })
  }, [models])

  const requestCreatorScroll = () => setCreatorScrollRequest(n => n + 1)

  return { newAgent, setNewAgent, creatorOpen, setCreatorOpen, requestCreatorScroll }
}
