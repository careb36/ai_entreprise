/**
 * A structured form for a specification a person writes or edits: the title
 * and summary, the requirements with their acceptance criteria (given, when,
 * then), the quality attributes with a measurable target, and the
 * constraints. Ids (REQ-1, REQ-1.1, NFR-1) are assigned by the backend when
 * the body is saved, so the form never shows them.
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Loader2, Plus, X } from 'lucide-react'
import type { SpecificationBody } from '../services/specifications'

type Requirement = SpecificationBody['requirements'][number]
type Criterion = Requirement['criteria'][number]
type Quality = SpecificationBody['quality'][number]

const EMPTY_CRITERION: Criterion = { given: '', when: '', then: '' }
const EMPTY_REQUIREMENT: Requirement = { title: '', description: '', criteria: [{ ...EMPTY_CRITERION }] }
const EMPTY_QUALITY: Quality = { attribute: '', target: '' }

export const EMPTY_SPECIFICATION: SpecificationBody = {
  title: '', summary: '', requirements: [{ ...EMPTY_REQUIREMENT, criteria: [{ ...EMPTY_CRITERION }] }], quality: [], constraints: [],
}

function replaceAt<T>(items: T[], index: number, item: T): T[] {
  return items.map((current, i) => (i === index ? item : current))
}

function removeAt<T>(items: T[], index: number): T[] {
  return items.filter((_, i) => i !== index)
}

function clean(body: SpecificationBody): SpecificationBody {
  return {
    title: body.title.trim(),
    summary: body.summary.trim(),
    requirements: body.requirements.map(requirement => ({
      title: requirement.title.trim(),
      description: requirement.description.trim(),
      criteria: requirement.criteria.map(criterion => ({
        given: criterion.given.trim(), when: criterion.when.trim(), then: criterion.then.trim(),
      })),
    })),
    quality: body.quality.map(quality => ({ attribute: quality.attribute.trim(), target: quality.target.trim() })),
    constraints: body.constraints.map(constraint => constraint.trim()).filter(Boolean),
  }
}

function RemoveButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} aria-label={label} title={label} className="btn btn-ghost btn-sm shrink-0 text-gray-400 hover:text-red-300">
      <X size={14} aria-hidden="true" />
    </button>
  )
}

function CriterionRow({ index, requirementIndex, criterion, onChange, onRemove }: {
  index: number; requirementIndex: number; criterion: Criterion
  onChange: (criterion: Criterion) => void; onRemove: (() => void) | null
}) {
  const { t } = useTranslation()
  const n = index + 1
  const r = requirementIndex + 1
  return (
    <li className="flex flex-wrap items-start gap-2 sm:flex-nowrap">
      <label className="block min-w-0 flex-1">
        <span className="input-label">{t('specifications.requirements.given')}</span>
        <input value={criterion.given} onChange={e => onChange({ ...criterion, given: e.target.value })} className="input w-full"
          aria-label={t('specifications.editor.criterionField', { field: t('specifications.requirements.given'), r, n })} />
      </label>
      <label className="block min-w-0 flex-1">
        <span className="input-label">{t('specifications.requirements.when')}</span>
        <input value={criterion.when} onChange={e => onChange({ ...criterion, when: e.target.value })} className="input w-full"
          aria-label={t('specifications.editor.criterionField', { field: t('specifications.requirements.when'), r, n })} />
      </label>
      <label className="block min-w-0 flex-1">
        <span className="input-label">{t('specifications.requirements.then')}</span>
        <input value={criterion.then} onChange={e => onChange({ ...criterion, then: e.target.value })} className="input w-full"
          aria-label={t('specifications.editor.criterionField', { field: t('specifications.requirements.then'), r, n })} />
      </label>
      {onRemove && <div className="pt-6"><RemoveButton label={t('specifications.editor.removeCriterion', { r, n })} onClick={onRemove} /></div>}
    </li>
  )
}

function RequirementCard({ index, requirement, onChange, onRemove }: {
  index: number; requirement: Requirement; onChange: (requirement: Requirement) => void; onRemove: (() => void) | null
}) {
  const { t } = useTranslation()
  const n = index + 1
  return (
    <li className="rounded-lg border border-surface-700 bg-surface-900/60 p-3 space-y-3">
      <div className="flex items-start gap-2">
        <h4 className="flex-1 text-xs font-semibold uppercase tracking-wider text-gray-400">{t('specifications.editor.requirement', { n })}</h4>
        {onRemove && <RemoveButton label={t('specifications.editor.removeRequirement', { n })} onClick={onRemove} />}
      </div>
      <label className="block">
        <span className="input-label">{t('specifications.editor.requirementTitle')}</span>
        <input value={requirement.title} onChange={e => onChange({ ...requirement, title: e.target.value })} required className="input w-full"
          aria-label={t('specifications.editor.requirementTitleOf', { n })} />
      </label>
      <label className="block">
        <span className="input-label">{t('specifications.editor.description')}</span>
        <textarea value={requirement.description} onChange={e => onChange({ ...requirement, description: e.target.value })} rows={2} className="input w-full"
          aria-label={t('specifications.editor.descriptionOf', { n })} />
      </label>
      <div className="space-y-2">
        <p className="text-xs font-semibold text-gray-300">{t('specifications.requirements.criteria')}</p>
        <ul className="space-y-2">
          {requirement.criteria.map((criterion, i) => (
            <CriterionRow key={i} index={i} requirementIndex={index} criterion={criterion}
              onChange={next => onChange({ ...requirement, criteria: replaceAt(requirement.criteria, i, next) })}
              onRemove={requirement.criteria.length > 1 ? () => onChange({ ...requirement, criteria: removeAt(requirement.criteria, i) }) : null} />
          ))}
        </ul>
        <button type="button" onClick={() => onChange({ ...requirement, criteria: [...requirement.criteria, { ...EMPTY_CRITERION }] })}
          className="btn btn-ghost btn-sm">
          <Plus size={14} aria-hidden="true" /> {t('specifications.editor.addCriterion')}
        </button>
      </div>
    </li>
  )
}

export function SpecificationEditor({ initial, onSave, onCancel, saving, error }: {
  initial?: SpecificationBody
  onSave: (body: SpecificationBody) => void
  onCancel: () => void
  saving: boolean
  error: string | null
}) {
  const { t } = useTranslation()
  const [body, setBody] = useState<SpecificationBody>(initial ?? EMPTY_SPECIFICATION)
  const complete = body.title.trim() !== '' && body.requirements.length > 0
    && body.requirements.every(r => r.title.trim() !== '' && r.criteria.every(c => c.given.trim() && c.when.trim() && c.then.trim()))

  return (
    <form aria-label={t('specifications.editor.formLabel')} onSubmit={e => { e.preventDefault(); if (complete) onSave(clean(body)) }}
      className="panel p-4 space-y-4">
      <p className="text-xs text-gray-400">{t('specifications.editor.idsNote')}</p>
      <label className="block">
        <span className="input-label">{t('specifications.editor.title')}</span>
        <input value={body.title} onChange={e => setBody({ ...body, title: e.target.value })} required maxLength={200} className="input w-full" />
      </label>
      <label className="block">
        <span className="input-label">{t('specifications.editor.summary')}</span>
        <textarea value={body.summary} onChange={e => setBody({ ...body, summary: e.target.value })} rows={3} className="input w-full" />
      </label>

      <section aria-label={t('specifications.editor.requirements')} className="space-y-2">
        <h3 className="text-sm font-semibold text-white">{t('specifications.editor.requirements')}</h3>
        <ul className="space-y-2">
          {body.requirements.map((requirement, i) => (
            <RequirementCard key={i} index={i} requirement={requirement}
              onChange={next => setBody({ ...body, requirements: replaceAt(body.requirements, i, next) })}
              onRemove={body.requirements.length > 1 ? () => setBody({ ...body, requirements: removeAt(body.requirements, i) }) : null} />
          ))}
        </ul>
        <button type="button" className="btn btn-secondary btn-sm"
          onClick={() => setBody({ ...body, requirements: [...body.requirements, { ...EMPTY_REQUIREMENT, criteria: [{ ...EMPTY_CRITERION }] }] })}>
          <Plus size={14} aria-hidden="true" /> {t('specifications.editor.addRequirement')}
        </button>
      </section>

      <section aria-label={t('specifications.editor.quality')} className="space-y-2">
        <h3 className="text-sm font-semibold text-white">{t('specifications.editor.quality')}</h3>
        <p className="text-xs text-gray-500">{t('specifications.editor.qualityHint')}</p>
        <ul className="space-y-2">
          {body.quality.map((quality, i) => (
            <li key={i} className="flex items-start gap-2">
              <label className="block flex-1">
                <span className="input-label">{t('specifications.quality.attribute')}</span>
                <input value={quality.attribute} aria-label={t('specifications.editor.attributeOf', { n: i + 1 })} className="input w-full"
                  onChange={e => setBody({ ...body, quality: replaceAt(body.quality, i, { ...quality, attribute: e.target.value }) })} />
              </label>
              <label className="block flex-1">
                <span className="input-label">{t('specifications.quality.target')}</span>
                <input value={quality.target} aria-label={t('specifications.editor.targetOf', { n: i + 1 })} className="input w-full"
                  onChange={e => setBody({ ...body, quality: replaceAt(body.quality, i, { ...quality, target: e.target.value }) })} />
              </label>
              <div className="pt-6">
                <RemoveButton label={t('specifications.editor.removeQuality', { n: i + 1 })} onClick={() => setBody({ ...body, quality: removeAt(body.quality, i) })} />
              </div>
            </li>
          ))}
        </ul>
        <button type="button" className="btn btn-secondary btn-sm" onClick={() => setBody({ ...body, quality: [...body.quality, { ...EMPTY_QUALITY }] })}>
          <Plus size={14} aria-hidden="true" /> {t('specifications.editor.addQuality')}
        </button>
      </section>

      <section aria-label={t('specifications.editor.constraints')} className="space-y-2">
        <h3 className="text-sm font-semibold text-white">{t('specifications.editor.constraints')}</h3>
        <ul className="space-y-2">
          {body.constraints.map((constraint, i) => (
            <li key={i} className="flex items-center gap-2">
              <input value={constraint} aria-label={t('specifications.editor.constraintOf', { n: i + 1 })} className="input w-full"
                onChange={e => setBody({ ...body, constraints: replaceAt(body.constraints, i, e.target.value) })} />
              <RemoveButton label={t('specifications.editor.removeConstraint', { n: i + 1 })} onClick={() => setBody({ ...body, constraints: removeAt(body.constraints, i) })} />
            </li>
          ))}
        </ul>
        <button type="button" className="btn btn-secondary btn-sm" onClick={() => setBody({ ...body, constraints: [...body.constraints, ''] })}>
          <Plus size={14} aria-hidden="true" /> {t('specifications.editor.addConstraint')}
        </button>
      </section>

      <div className="flex flex-wrap items-center gap-3 pt-1">
        <button type="submit" disabled={saving || !complete} className="btn btn-primary">
          {saving ? <Loader2 size={15} className="animate-spin" aria-hidden="true" /> : null}
          {saving ? t('specifications.editor.saving') : t('specifications.editor.save')}
        </button>
        <button type="button" onClick={onCancel} disabled={saving} className="btn btn-ghost">{t('common.cancel')}</button>
        {!complete && <span className="text-xs text-gray-500">{t('specifications.editor.incomplete')}</span>}
        {error && <span role="alert" className="text-sm text-red-300">{error}</span>}
      </div>
    </form>
  )
}
