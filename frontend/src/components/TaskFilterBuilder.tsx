import { useId } from 'react'
import { useTranslation } from 'react-i18next'
import { Plus, X } from 'lucide-react'
import { FILTER_FIELDS, type FilterField, type FilterOperator } from '../services/api'
import { FIELD_CHOICES, FIELD_OPERATORS, emptyRow, type FilterJoin, type FilterRow } from '../lib/taskFilters'

/**
 * The conditions of a filter over a sprint's tasks (issue #535): one row per
 * condition, joined by all (AND) or any (OR). Every control has a label and is
 * reached by keyboard; the fields and operators are the server's allowlist.
 */
export function TaskFilterBuilder({
  rows, join, onChange, disabled = false,
}: {
  rows: FilterRow[]
  join: FilterJoin
  onChange: (rows: FilterRow[], join: FilterJoin) => void
  disabled?: boolean
}) {
  const { t } = useTranslation()
  const baseId = useId()

  const update = (index: number, patch: Partial<FilterRow>) => {
    const next = rows.map((row, i) => {
      if (i !== index) return row
      if (patch.field && patch.field !== row.field) return emptyRow(patch.field)
      return { ...row, ...patch }
    })
    onChange(next, join)
  }

  return (
    <fieldset className="space-y-2" disabled={disabled}>
      <legend className="sr-only">{t('taskViews.filters')}</legend>
      {rows.length > 1 && (
        <label className="flex items-center gap-2 text-xs text-gray-400">
          {t('taskViews.join')}
          <select className="select text-xs py-1" value={join}
            onChange={e => onChange(rows, e.target.value as FilterJoin)}>
            <option value="and">{t('taskViews.joinAll')}</option>
            <option value="or">{t('taskViews.joinAny')}</option>
          </select>
        </label>
      )}
      {rows.map((row, index) => {
        const id = `${baseId}-${index}`
        const choices = FIELD_CHOICES[row.field]
        const fieldLabel = t(`taskViews.field.${row.field}`)
        return (
          <div key={index} className="flex flex-wrap items-center gap-1.5" data-testid="filter-row">
            <label htmlFor={`${id}-field`} className="sr-only">{t('taskViews.fieldLabel', { n: index + 1 })}</label>
            <select id={`${id}-field`} className="select text-xs py-1" value={row.field}
              onChange={e => update(index, { field: e.target.value as FilterField })}>
              {FILTER_FIELDS.map(field => (
                <option key={field} value={field}>{t(`taskViews.field.${field}`)}</option>
              ))}
            </select>
            <label htmlFor={`${id}-op`} className="sr-only">{t('taskViews.operatorLabel', { field: fieldLabel })}</label>
            <select id={`${id}-op`} className="select text-xs py-1" value={row.op}
              onChange={e => update(index, { op: e.target.value as FilterOperator })}>
              {FIELD_OPERATORS[row.field].map(op => (
                <option key={op} value={op}>{t(`taskViews.op.${op}`)}</option>
              ))}
            </select>
            <label htmlFor={`${id}-value`} className="sr-only">{t('taskViews.valueLabel', { field: fieldLabel })}</label>
            {choices && row.op !== 'in' ? (
              <select id={`${id}-value`} className="select text-xs py-1" value={row.value}
                onChange={e => update(index, { value: e.target.value })}>
                {choices.map(choice => (
                  <option key={choice} value={choice}>{t(`taskViews.value.${choice}`, choice)}</option>
                ))}
              </select>
            ) : (
              <input id={`${id}-value`} className="input text-xs py-1 w-40" value={row.value}
                placeholder={row.op === 'in' ? t('taskViews.listPlaceholder') : t(`taskViews.placeholder.${row.field}`, '')}
                onChange={e => update(index, { value: e.target.value })} />
            )}
            <button type="button" className="btn-ghost btn-sm" aria-label={t('taskViews.removeCondition', { n: index + 1 })}
              onClick={() => onChange(rows.filter((_, i) => i !== index), join)}>
              <X size={12} aria-hidden="true" />
            </button>
          </div>
        )
      })}
      <button type="button" className="btn-ghost btn-sm inline-flex items-center gap-1"
        onClick={() => onChange([...rows, emptyRow()], join)}>
        <Plus size={12} aria-hidden="true" />{t('taskViews.addCondition')}
      </button>
    </fieldset>
  )
}
