import { Suspense, useMemo, useReducer } from 'react'
import { Canvas } from '@react-three/fiber'
import { OrbitControls } from '@react-three/drei'
import { Controllers, Hands, VRButton, XR, useXR } from '@react-three/xr'
import { demoWorld } from './demo'
import { parseFlags } from './flags'
import { parseLiveConfig } from './live'
import { blockedForPerson, pendingRuns, reduce } from './model'
import { Room } from './scene/Room'
import { useDemoTimeline } from './useDemoTimeline'
import { useLiveWorld, type LiveStatus } from './useLiveWorld'

/** Who the pinches are recorded as in the demo; live mode takes `&person=`. */
export const PERSON = 'You'

const overlay: React.CSSProperties = {
  position: 'absolute',
  top: 12,
  left: 16,
  zIndex: 1,
  maxWidth: 440,
  pointerEvents: 'none',
  textShadow: '0 1px 2px rgba(0,0,0,0.8)',
}

const STATUS_TEXT: Record<LiveStatus, string> = {
  demo: 'demo company',
  loading: 'connecting to the backend…',
  live: 'live',
  error: 'the backend could not be read; showing the demo company',
}

/** On a desktop the camera orbits; in a headset the person's head is the camera. */
function DesktopControls() {
  const presenting = useXR(state => state.isPresenting)
  return presenting ? null : <OrbitControls target={[0, 1.2, -1]} enablePan={false} minDistance={0.2} maxDistance={3} />
}

export default function App() {
  const [world, dispatch] = useReducer(reduce, undefined, demoWorld)
  const config = useMemo(() => parseLiveConfig(window.location.search), [])
  const flags = useMemo(() => parseFlags(window.location.search), [])
  const { perform, status } = useLiveWorld(config, dispatch)
  // The demo company keeps working for a minute after the room opens.
  useDemoTimeline(status === 'demo', dispatch)
  const waiting = pendingRuns(world).length + blockedForPerson(world).length

  return (
    <>
      <header style={overlay}>
        <h1 style={{ margin: 0, fontSize: 20 }}>Glassroom VR</h1>
        <p style={{ margin: '4px 0', fontSize: 14 }}>
          {world.company} · {world.sprint} · {STATUS_TEXT[status]}
        </p>
        <p style={{ margin: 0, fontSize: 13, opacity: 0.8 }}>
          {waiting} waiting for you · pinch a card to pick it up, pinch again to put it down · drag to look around on a desktop
        </p>
      </header>
      <VRButton />
      <Canvas camera={{ position: [0, 1.25, 0.2], fov: 70 }} dpr={[1, 1.5]} gl={{ antialias: true, powerPreference: 'high-performance' }}>
        <XR>
          <Controllers />
          {/* The hand models ship with the room: nothing fetched from a CDN in the headset. */}
          <Hands modelLeft={`${import.meta.env.BASE_URL}hands/left.glb`} modelRight={`${import.meta.env.BASE_URL}hands/right.glb`} />
          <Suspense fallback={null}>
            <Room world={world} dispatch={perform} person={config?.person ?? PERSON} stats={flags.stats} gaze={flags.gaze} />
          </Suspense>
          <DesktopControls />
        </XR>
      </Canvas>
    </>
  )
}
