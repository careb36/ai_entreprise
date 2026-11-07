/**
 * A structured form for an architecture a person writes or edits: the title
 * and overview, the modules (name, path, responsibility, which other modules
 * each depends on and which requirements it covers), the interfaces (name,
 * kind, the module that provides it, its contract and the modules that
 * consume it), the decisions and the conventions. Modules are named here and
 * ids (MOD-1, IF-1, ADR-1) are assigned by the backend when the body is
 * saved, which also maps the names to ids, so the form never shows them.
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Loader2, Plus, X } from 'lucide-react'
import { type ArchitectureBody, INTERFACE_KINDS } from '../services/architectures'
import type { Requirement } from '../services/specifications'

type Module = ArchitectureBody['modules'][number]
type Interface = ArchitectureBody['interfaces'][number]
type Decision = ArchitectureBody['decisions'][number]

const EMPTY_MODULE: Module = { name: '', responsibility: '', path: '', depends_on: [], covers: [] }
const EMPTY_INTERFACE: Interface = { name: '', kind: 'http', module: '', contract: '', consumers: [] }
const EMPTY_DECISION: Decision = { title: '', context: '', decision: '', consequences: '' }

export const EMPTY_ARCHITECTURE: ArchitectureBody = {
  title: '', overview: '', modules: [{ ...EMPTY_MODULE }], interfaces: [], decisions: [], conventions: [],
}

function replaceAt<T>(items: T[], index: number, item: T): T[] {
  return items.map((current, i) => (i === index ? item : current))
}

function removeAt<T>(items: T[], index: number): T[] {
  return items.filter((_, i) => i !== index)
}

function toggle(items: string[], item: string, on: boolean): string[] {
  if (on) return items.includes(item) ? items : [...items, item]
  return items.filter(current => current !== item)
}

function clean(body: ArchitectureBody): ArchitectureBody {
  const names = new Set(body.modules.map(m => m.name.trim()).filter(Boolean))
  const known = (items: string[]) => items.filter(item => names.has(item))
  return {
    title: body.title.trim(),
    overview: body.overview.trim(),
    modules: body.modules.map(module => ({
      name: module.name.trim(),
      responsibility: module.responsibility.trim(),
      path: module.path.trim(),
      depends_on: known(module.depends_on).filter(name => name !== module.name.trim()),
      covers: [...module.covers],
    })),
    interfaces: body.interfaces.map(iface => ({
      name: iface.name.trim(), kind: iface.kind, module: iface.module, contract: iface.contract.trim(), consumers: known(iface.consumers),
    })),
    decisions: body.decisions.map(decision => ({
      title: decision.title.trim(), context: decision.context.trim(), decision: decision.decision.trim(), consequences: decision.consequences.trim(),
    })),
    conventions: body.conventions.map(convention => convention.trim()).filter(Boolean),
  }
}

function RemoveButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} aria-label={label} title={label} className="btn btn-ghost btn-sm shrink-0 text-gray-400 hover:text-red-300">
      <X size={14} aria-hidden="true" />
    </button>
  )
}

/** A set of names to pick from, as checkboxes: `<select multiple>` hides what is chosen. */
function Picker({ legend, options, selected, itemLabel, onChange }: {
  legend: string; options: string[]; selected: string[]; itemLabel: (option: string) => string; onChange: (selected: string[]) => void
}) {
  const { t } = useTranslation()
  return (
    <fieldset className="space-y-1">
      <legend className="input-label">{legend}</legend>
      {options.length === 0 ? (
        <p className="text-xs text-gray-500">{t('architectures.editor.nothingToPick')}</p>
      ) : (
        <div className="flex flex-wrap gap-x-3 gap-y-1">
          {options.map(option => (
            <label key={option} className="flex items-center gap-1 text-xs text-gray-200">
              <input type="checkbox" checked={selected.includes(option)} aria-label={itemLabel(option)}
                onChange={e => onChange(toggle(selected, option, e.target.checked))} />
              <span>{option}</span>
            </label>
          ))}
        </div>
      )}
    </fieldset>
  )
}

function ModuleCard({ index, module, others, requirementIds, onChange, onRemove }: {
  index: number; module: Module; others: string[]; requirementIds: string[]
  onChange: (module: Module) => void; onRemove: (() => void) | null
}) {
  const { t } = useTranslation()
  const n = index + 1
  return (
    <li className="rounded-lg border border-surface-700 bg-surface-900/60 p-3 space-y-3">
      <div className="flex items-start gap-2">
        <h4 className="flex-1 text-xs font-semibold uppercase tracking-wider text-gray-400">{t('architectures.editor.module', { n })}</h4>
        {onRemove && <RemoveButton label={t('architectures.editor.removeModule', { n })} onClick={onRemove} />}
      </div>
      <div className="flex flex-wrap gap-2 sm:flex-nowrap">
        <label className="block min-w-0 flex-1">
          <span className="input-label">{t('architectures.editor.moduleName')}</span>
          <input value={module.name} onChange={e => onChange({ ...module, name: e.target.value })} required className="input w-full"
            aria-label={t('architectures.editor.moduleNameOf', { n })} />
        </label>
        <label className="block min-w-0 flex-1">
          <span className="input-label">{t('architectures.editor.modulePath')}</span>
          <input value={module.path} onChange={e => onChange({ ...module, path: e.target.value })} required className="input w-full font-mono"
            aria-label={t('architectures.editor.modulePathOf', { n })} />
        </label>
      </div>
      <label className="block">
        <span className="input-label">{t('architectures.editor.responsibility')}</span>
        <textarea value={module.responsibility} onChange={e => onChange({ ...module, responsibility: e.target.value })} rows={2} className="input w-full"
          aria-label={t('architectures.editor.responsibilityOf', { n })} />
      </label>
      <Picker legend={t('architectures.editor.dependsOn')} options={others} selected={module.depends_on}
        itemLabel={name => t('architectures.editor.dependsOnItem', { n, name })} onChange={depends_on => onChange({ ...module, depends_on })} />
      <Picker legend={t('architectures.editor.covers')} options={requirementIds} selected={module.covers}
        itemLabel={id => t('architectures.editor.coversItem', { n, id })} onChange={covers => onChange({ ...module, covers })} />
    </li>
  )
}

function InterfaceCard({ index, iface, moduleNames, onChange, onRemove }: {
  index: number; iface: Interface; moduleNames: string[]; onChange: (iface: Interface) => void; onRemove: () => void
}) {
  const { t } = useTranslation()
  const n = index + 1
  return (
    <li className="rounded-lg border border-surface-700 bg-surface-900/60 p-3 space-y-3">
      <div className="flex items-start gap-2">
        <h4 className="flex-1 text-xs font-semibold uppercase tracking-wider text-gray-400">{t('architectures.editor.interface', { n })}</h4>
        <RemoveButton label={t('architectures.editor.removeInterface', { n })} onClick={onRemove} />
      </div>
      <div className="flex flex-wrap gap-2 sm:flex-nowrap">
        <label className="block min-w-0 flex-1">
          <span className="input-label">{t('architectures.editor.interfaceName')}</span>
          <input value={iface.name} onChange={e => onChange({ ...iface, name: e.target.value })} required className="input w-full"
            aria-label={t('architectures.editor.interfaceNameOf', { n })} />
        </label>
        <label className="block min-w-0 flex-1">
          <span className="input-label">{t('architectures.editor.kind')}</span>
          <select value={iface.kind} onChange={e => onChange({ ...iface, kind: e.target.value as Interface['kind'] })} className="input w-full"
            aria-label={t('architectures.editor.kindOf', { n })}>
            {INTERFACE_KINDS.map(kind => <option key={kind} value={kind}>{t(`architectures.kinds.${kind}`, { defaultValue: kind })}</option>)}
          </select>
        </label>
        <label className="block min-w-0 flex-1">
          <span className="input-label">{t('architectures.editor.providedBy')}</span>
          <select value={iface.module} onChange={e => onChange({ ...iface, module: e.target.value })} required className="input w-full"
            aria-label={t('architectures.editor.providedByOf', { n })}>
            <option value="">{t('architectures.editor.pickModule')}</option>
            {moduleNames.map(name => <option key={name} value={name}>{name}</option>)}
          </select>
        </label>
      </div>
      <label className="block">
        <span className="input-label">{t('architectures.editor.contract')}</span>
        <textarea value={iface.contract} onChange={e => onChange({ ...iface, contract: e.target.value })} rows={3} className="input w-full font-mono text-xs"
          aria-label={t('architectures.editor.contractOf', { n })} />
      </label>
      <Picker legend={t('architectures.editor.consumers')} options={moduleNames.filter(name => name !== iface.module)} selected={iface.consumers}
        itemLabel={name => t('architectures.editor.consumersItem', { n, name })} onChange={consumers => onChange({ ...iface, consumers })} />
    </li>
  )
}

function DecisionCard({ index, decision, onChange, onRemove }: {
  index: number; decision: Decision; onChange: (decision: Decision) => void; onRemove: () => void
}) {
  const { t } = useTranslation()
  const n = index + 1
  const fields: (keyof Decision)[] = ['context', 'decision', 'consequences']
  return (
    <li className="rounded-lg border border-surface-700 bg-surface-900/60 p-3 space-y-3">
      <div className="flex items-start gap-2">
        <h4 className="flex-1 text-xs font-semibold uppercase tracking-wider text-gray-400">{t('architectures.editor.decision', { n })}</h4>
        <RemoveButton label={t('architectures.editor.removeDecision', { n })} onClick={onRemove} />
      </div>
      <label className="block">
        <span className="input-label">{t('architectures.editor.decisionTitle')}</span>
        <input value={decision.title} onChange={e => onChange({ ...decision, title: e.target.value })} required className="input w-full"
          aria-label={t('architectures.editor.decisionTitleOf', { n })} />
      </label>
      {fields.map(field => (
        <label key={field} className="block">
          <span className="input-label">{t(`architectures.decisions.${field}`, { defaultValue: field })}</span>
          <textarea value={decision[field]} onChange={e => onChange({ ...decision, [field]: e.target.value })} rows={2} className="input w-full"
            aria-label={t('architectures.editor.decisionField', { field: t(`architectures.decisions.${field}`, { defaultValue: field }), n })} />
        </label>
      ))}
    </li>
  )
}

export function ArchitectureEditor({ initial, requirements, onSave, onCancel, saving, error }: {
  initial?: ArchitectureBody
  /** The specification's requirements, whose ids a module covers. */
  requirements: Requirement[]
  onSave: (body: ArchitectureBody) => void
  onCancel: () => void
  saving: boolean
  error: string | null
}) {
  const { t } = useTranslation()
  const [body, setBody] = useState<ArchitectureBody>(initial ?? EMPTY_ARCHITECTURE)
  const moduleNames = body.modules.map(m => m.name.trim()).filter(Boolean)
  const requirementIds = requirements.map(r => r.id)
  const complete = body.title.trim() !== '' && body.modules.length > 0
    && body.modules.every(m => m.name.trim() !== '' && m.path.trim() !== '')
    && body.interfaces.every(i => i.name.trim() !== '' && moduleNames.includes(i.module))
    && body.decisions.every(d => d.title.trim() !== '')

  return (
    <form aria-label={t('architectures.editor.formLabel')} onSubmit={e => { e.preventDefault(); if (complete) onSave(clean(body)) }}
      className="card p-4 space-y-4">
      <p className="text-xs text-gray-500">{t('architectures.editor.idsNote')}</p>
      <label className="block">
        <span className="input-label">{t('architectures.editor.title')}</span>
        <input value={body.title} onChange={e => setBody({ ...body, title: e.target.value })} required maxLength={200} className="input w-full" />
      </label>
      <label className="block">
        <span className="input-label">{t('architectures.editor.overview')}</span>
        <textarea value={body.overview} onChange={e => setBody({ ...body, overview: e.target.value })} rows={3} className="input w-full" />
      </label>

      <section aria-label={t('architectures.modules.title')} className="space-y-2">
        <h3 className="text-sm font-semibold text-white">{t('architectures.modules.title')}</h3>
        <ul className="space-y-2">
          {body.modules.map((module, i) => (
            <ModuleCard key={i} index={i} module={module} requirementIds={requirementIds}
              others={moduleNames.filter(name => name !== module.name.trim())}
              onChange={next => setBody({ ...body, modules: replaceAt(body.modules, i, next) })}
              onRemove={body.modules.length > 1 ? () => setBody({ ...body, modules: removeAt(body.modules, i) }) : null} />
          ))}
        </ul>
        <button type="button" className="btn btn-secondary btn-sm" onClick={() => setBody({ ...body, modules: [...body.modules, { ...EMPTY_MODULE }] })}>
          <Plus size={14} aria-hidden="true" /> {t('architectures.editor.addModule')}
        </button>
      </section>

      <section aria-label={t('architectures.interfaces.title')} className="space-y-2">
        <h3 className="text-sm font-semibold text-white">{t('architectures.interfaces.title')}</h3>
        <ul className="space-y-2">
          {body.interfaces.map((iface, i) => (
            <InterfaceCard key={i} index={i} iface={iface} moduleNames={moduleNames}
              onChange={next => setBody({ ...body, interfaces: replaceAt(body.interfaces, i, next) })}
              onRemove={() => setBody({ ...body, interfaces: removeAt(body.interfaces, i) })} />
          ))}
        </ul>
        <button type="button" className="btn btn-secondary btn-sm" onClick={() => setBody({ ...body, interfaces: [...body.interfaces, { ...EMPTY_INTERFACE }] })}>
          <Plus size={14} aria-hidden="true" /> {t('architectures.editor.addInterface')}
        </button>
      </section>

      <section aria-label={t('architectures.decisions.title')} className="space-y-2">
        <h3 className="text-sm font-semibold text-white">{t('architectures.decisions.title')}</h3>
        <ul className="space-y-2">
          {body.decisions.map((decision, i) => (
            <DecisionCard key={i} index={i} decision={decision}
              onChange={next => setBody({ ...body, decisions: replaceAt(body.decisions, i, next) })}
              onRemove={() => setBody({ ...body, decisions: removeAt(body.decisions, i) })} />
          ))}
        </ul>
        <button type="button" className="btn btn-secondary btn-sm" onClick={() => setBody({ ...body, decisions: [...body.decisions, { ...EMPTY_DECISION }] })}>
          <Plus size={14} aria-hidden="true" /> {t('architectures.editor.addDecision')}
        </button>
      </section>

      <section aria-label={t('architectures.conventions.title')} className="space-y-2">
        <h3 className="text-sm font-semibold text-white">{t('architectures.conventions.title')}</h3>
        <ul className="space-y-2">
          {body.conventions.map((convention, i) => (
            <li key={i} className="flex items-center gap-2">
              <input value={convention} aria-label={t('architectures.editor.conventionOf', { n: i + 1 })} className="input w-full"
                onChange={e => setBody({ ...body, conventions: replaceAt(body.conventions, i, e.target.value) })} />
              <RemoveButton label={t('architectures.editor.removeConvention', { n: i + 1 })} onClick={() => setBody({ ...body, conventions: removeAt(body.conventions, i) })} />
            </li>
          ))}
        </ul>
        <button type="button" className="btn btn-secondary btn-sm" onClick={() => setBody({ ...body, conventions: [...body.conventions, ''] })}>
          <Plus size={14} aria-hidden="true" /> {t('architectures.editor.addConvention')}
        </button>
      </section>

      <div className="flex flex-wrap items-center gap-3 pt-1">
        <button type="submit" disabled={saving || !complete} className="btn btn-primary">
          {saving ? <Loader2 size={15} className="animate-spin" aria-hidden="true" /> : null}
          {saving ? t('specifications.editor.saving') : t('specifications.editor.save')}
        </button>
        <button type="button" onClick={onCancel} disabled={saving} className="btn btn-ghost">{t('common.cancel')}</button>
        {!complete && <span className="text-xs text-gray-500">{t('architectures.editor.incomplete')}</span>}
        {error && <span role="alert" className="text-sm text-red-300">{error}</span>}
      </div>
    </form>
  )
}
