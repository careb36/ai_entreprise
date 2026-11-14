import { useEffect, useRef, useState } from 'react'

/**
 * Two-step delete: the first click arms the button, a second click within 3s
 * confirms and calls `onConfirm`; otherwise the button disarms itself.
 */
export function useConfirmDelete(onConfirm: (agentId: number) => void) {
  const [confirmingDeleteId, setConfirmingDeleteId] = useState<number | null>(null)
  const confirmTimeoutRef = useRef<number | null>(null)

  // Cleanup confirm-delete timer on unmount.
  useEffect(() => {
    return () => {
      if (confirmTimeoutRef.current !== null) window.clearTimeout(confirmTimeoutRef.current)
    }
  }, [])

  function handleDeleteRequest(agentId: number) {
    if (confirmingDeleteId === agentId) {
      // Confirm.
      if (confirmTimeoutRef.current !== null) {
        window.clearTimeout(confirmTimeoutRef.current)
        confirmTimeoutRef.current = null
      }
      setConfirmingDeleteId(null)
      onConfirm(agentId)
      return
    }
    // Arm.
    setConfirmingDeleteId(agentId)
    if (confirmTimeoutRef.current !== null) window.clearTimeout(confirmTimeoutRef.current)
    confirmTimeoutRef.current = window.setTimeout(() => {
      setConfirmingDeleteId(prev => (prev === agentId ? null : prev))
      confirmTimeoutRef.current = null
    }, 3000)
  }

  return { confirmingDeleteId, handleDeleteRequest }
}
