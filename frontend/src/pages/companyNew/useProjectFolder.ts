import { useRef, useState } from 'react'
import { api, ApiError, errorDetail, type DetectedStack, type WorkspaceInspection } from '../../services/api'
import { clientPlatform } from '../../lib/workspace'
import { useWorkspaceStatus } from '../../hooks/useQueries'
import type { InspectOutcome } from './types'

/**
 * Step 0's state: the folder path, what the workspace daemon observed about it,
 * the folder picker, and the stack a scan detected.
 */
export function useProjectFolder() {
  // Step 0 — Project: a host-native path on the user's computer, so the field starts empty.
  const [projectPath, setProjectPath] = useState('')
  const [discoveryResult, setDiscoveryResult] = useState<DetectedStack | null>(null)
  const [pickerOpen, setPickerOpen] = useState(false)
  const [inspected, setInspected] = useState<WorkspaceInspection | null>(null)
  const [inspecting, setInspecting] = useState(false)
  const [inspectError, setInspectError] = useState<{ status: number; message: string } | null>(null)
  const inspectRequest = useRef(0)
  const pendingInspect = useRef<{ path: string; promise: Promise<InspectOutcome> } | null>(null)
  const browseButtonRef = useRef<HTMLButtonElement>(null)
  const workspaceStatus = useWorkspaceStatus()
  const workspaceHost = workspaceStatus.data?.host ?? inspected?.host ?? null
  const hostPlatform = workspaceHost?.platform ?? clientPlatform()
  const selectDirectory = typeof window !== 'undefined' ? window.electronAPI?.selectDirectory : undefined

  /**
   * Asks the workspace daemon about `rawPath`. On success the input shows the
   * canonical path it returns. Errors (503: daemon not connected) are shown but
   * never block typing a path by hand.
   */
  const inspectPath = (rawPath: string): Promise<InspectOutcome> => {
    const path = rawPath.trim()
    if (!path) return Promise.resolve({ kind: 'error' })
    // Blurring the field and then clicking Scan asks about the same path twice.
    if (pendingInspect.current?.path === path) return pendingInspect.current.promise
    const request = ++inspectRequest.current
    setInspecting(true)
    setInspectError(null)
    const promise = (async (): Promise<InspectOutcome> => {
      try {
        const result = await api.inspectWorkspace(path)
        if (request !== inspectRequest.current) return { kind: 'stale' }
        setInspected(result)
        if (result.exists) setProjectPath(result.path)
        return { kind: 'ok', result }
      } catch (err) {
        if (request !== inspectRequest.current) return { kind: 'stale' }
        setInspected(null)
        setInspectError({ status: err instanceof ApiError ? err.status : 0, message: errorDetail(err) })
        return { kind: 'error' }
      } finally {
        if (request === inspectRequest.current) {
          setInspecting(false)
          pendingInspect.current = null
        }
      }
    })()
    pendingInspect.current = { path, promise }
    return promise
  }

  const chooseFolder = (path: string) => {
    setPickerOpen(false)
    setProjectPath(path)
    setDiscoveryResult(null)
    void inspectPath(path)
  }

  const handleBrowse = async () => {
    if (!selectDirectory) {
      setPickerOpen(true)
      return
    }
    const chosen = await selectDirectory()
    if (chosen) chooseFolder(chosen)
  }

  const closePicker = () => {
    setPickerOpen(false)
    browseButtonRef.current?.focus()
  }

  const handlePathChange = (value: string) => {
    inspectRequest.current++
    pendingInspect.current = null
    setProjectPath(value)
    setInspected(null)
    setInspectError(null)
    setInspecting(false)
  }

  const folderMissing = inspected !== null && (!inspected.exists || !inspected.is_dir)

  return {
    projectPath, setProjectPath,
    discoveryResult, setDiscoveryResult,
    pickerOpen, setPickerOpen,
    inspected, setInspected,
    inspecting, inspectError,
    browseButtonRef, workspaceStatus, workspaceHost, hostPlatform, selectDirectory,
    inspectPath, chooseFolder, handleBrowse, closePicker, handlePathChange, folderMissing,
  }
}

export type ProjectFolder = ReturnType<typeof useProjectFolder>
