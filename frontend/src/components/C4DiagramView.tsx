/**
 * One C4 diagram of a documentation pack, drawn as SVG from the diagram's data
 * (`lib/c4Layout.ts`). Labels are React text, so a module named `<script>` is
 * shown as those characters; the Mermaid source the repository gets is shown
 * beside it as text and never executed here.
 */
import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { NODE_HEIGHT, NODE_WIDTH, layoutC4 } from '../lib/c4Layout'
import type { C4Diagram, C4Kind } from '../services/documentation'

const FILL: Record<C4Kind, string> = {
  person: '#08427b',
  system: '#1168bd',
  external: '#6b7280',
  container: '#438dd5',
  datastore: '#2f6fa8',
  component: '#85bbf0',
}

function cut(text: string, max: number) {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text
}

export function C4DiagramView({ diagram }: { diagram: C4Diagram }) {
  const { t } = useTranslation()
  const [showSource, setShowSource] = useState(false)
  const arrow = useId().replace(/:/g, '')
  const layout = layoutC4(diagram)
  const groups = new Map(diagram.groups.map(g => [g.id, g.label]))
  return (
    <figure className="card p-4 space-y-3" aria-label={diagram.title}>
      <figcaption className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-sm font-semibold text-white">
          {diagram.title} <span className="text-xs text-gray-400">({t(`documentation.levels.${diagram.level}`)})</span>
        </span>
        <button type="button" className="text-xs underline text-gray-300" onClick={() => setShowSource(v => !v)}>
          {showSource ? t('documentation.hideSource') : t('documentation.showSource')}
        </button>
      </figcaption>
      {diagram.nodes.length === 0 ? (
        <p className="text-sm text-gray-400">{t('documentation.emptyDiagram')}</p>
      ) : (
        <div className="overflow-x-auto">
          <svg role="img" aria-label={diagram.title} width={layout.width} height={layout.height} viewBox={`0 0 ${layout.width} ${layout.height}`}>
            <defs>
              <marker id={arrow} viewBox="0 0 10 10" refX="10" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                <path d="M 0 0 L 10 5 L 0 10 z" fill="#9ca3af" />
              </marker>
            </defs>
            {layout.edges.map((edge, i) => (
              <g key={`e${i}`}>
                <line x1={edge.x1} y1={edge.y1} x2={edge.x2} y2={edge.y2} stroke="#9ca3af" strokeWidth={1.2} markerEnd={`url(#${arrow})`} />
                <text x={(edge.x1 + edge.x2) / 2} y={(edge.y1 + edge.y2) / 2 - 4} fill="#d1d5db" fontSize={10} textAnchor="middle">{cut(edge.label, 32)}</text>
              </g>
            ))}
            {layout.nodes.map(node => (
              <g key={node.id} data-testid="c4-node">
                <title>{[node.label, node.detail, node.group ? groups.get(node.group) : ''].filter(Boolean).join(' — ')}</title>
                <rect x={node.x} y={node.y} width={NODE_WIDTH} height={NODE_HEIGHT} rx={node.kind === 'person' ? 28 : 6} fill={FILL[node.kind] ?? FILL.container} stroke="#1f2937" />
                <text x={node.x + NODE_WIDTH / 2} y={node.y + 26} fill={node.kind === 'component' ? '#111827' : '#ffffff'} fontSize={12} fontWeight={600} textAnchor="middle">{cut(node.label, 26)}</text>
                <text x={node.x + NODE_WIDTH / 2} y={node.y + 44} fill={node.kind === 'component' ? '#1f2937' : '#e5e7eb'} fontSize={10} textAnchor="middle">
                  {node.group ? cut(groups.get(node.group) ?? '', 28) : t(`documentation.kinds.${node.kind}`)}
                </text>
              </g>
            ))}
          </svg>
        </div>
      )}
      {diagram.notes.length > 0 && (
        <ul className="list-disc pl-5 text-xs text-gray-400 space-y-1">
          {diagram.notes.map(note => <li key={note}>{note}</li>)}
        </ul>
      )}
      {showSource && <pre className="overflow-x-auto rounded bg-surface-900 p-3 text-xs text-gray-300" data-testid="mermaid-source">{diagram.mermaid}</pre>}
    </figure>
  )
}

export default C4DiagramView
