import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import App from './App'

// The scene needs a WebGL canvas and a headset; jsdom has neither. The room's
// model and layout are tested on their own; this checks what is drawn in HTML.
vi.mock('@react-three/fiber', () => ({ Canvas: () => null }))
vi.mock('@react-three/drei', () => ({ OrbitControls: () => null, Text: () => null, Grid: () => null }))
vi.mock('@react-three/xr', () => ({
  VRButton: () => <button type="button">Enter VR</button>,
  XR: ({ children }: { children?: unknown }) => <>{children as never}</>,
  Hands: () => null,
  Controllers: () => null,
  Interactive: ({ children }: { children?: unknown }) => <>{children as never}</>,
  useXR: () => false,
}))

describe('Glassroom VR', () => {
  it('opens on the demo company with what waits for the person, and offers to enter VR', () => {
    render(<App />)
    expect(screen.getByRole('heading', { name: 'Glassroom VR' })).toBeInTheDocument()
    expect(screen.getByText('Acme Payments · Sprint 3 — Checkout · demo company')).toBeInTheDocument()
    expect(screen.getByText(/^4 waiting for you/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Enter VR' })).toBeInTheDocument()
  })
})
