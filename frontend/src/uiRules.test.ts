import { readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { checkSource, checkViews, countByEntry } from './test/uiRules'

/**
 * The guard on the views' text and accessibility (`src/test/uiRules.ts`): what
 * the i18next lint rule cannot see, and the accessibility basics, in
 * `pages/` and `components/`. Its allowlist (`src/test/uiRulesBaseline.json`)
 * only shrinks, as `scripts/file_size_baseline.json` does.
 */
const SRC = dirname(fileURLToPath(import.meta.url))
const BASELINE = join(SRC, 'test', 'uiRulesBaseline.json')

const rules = (source: string) => checkSource('pages/Example.tsx', source).map(v => v.rule)

describe('the view rules', () => {
  it('reports a template with words shown as text or read aloud, not one with values only', () => {
    expect(rules('const A = ({ n }) => <p>{`${n} tasks left`}</p>')).toEqual(['text/template'])
    expect(rules('const A = ({ n, x }) => <p>{x ?? `ID ${n}`}</p>')).toEqual(['text/template'])
    expect(rules('const A = ({ d }) => <div title={`${d.date}: ${d.n} tokens`} />')).toEqual(['text/template'])
    expect(rules('const A = ({ a, b }) => <p>{`${a} · ${b}`}</p>')).toEqual([])
    expect(rules('const A = ({ n }) => <p>{t(`x.${n}`, { defaultValue: `the ${n} value` })}</p>')).toEqual([])
  })

  it('reports prose given to an attribute the lint rule does not check', () => {
    expect(rules('const A = () => <Panel heading="Recent activity" />')).toEqual(['text/attribute'])
    expect(rules('const A = ({ ok }) => <Panel emptyText={ok ? "All good" : "Nothing yet"} />')).toEqual(['text/attribute', 'text/attribute'])
    expect(rules('const A = () => <div aria-valuetext="Half done" />')).toEqual(['text/attribute'])
    expect(rules('const A = ({ s }) => <Panel heading={t("a.b")} kind="large" active={s === "Not started"} />')).toEqual([])
    expect(rules('const A = () => <a href="/x" rel="noopener noreferrer" className="Big link">{t("a")}</a>')).toEqual([])
  })

  it('reports an image without alt and an icon-only control without a name', () => {
    expect(rules('const A = () => <img src="/a.svg" />')).toEqual(['a11y/img-alt'])
    const icons = "import { X, Loader2 } from 'lucide-react'\n"
    expect(rules(`${icons}const A = () => <button onClick={f}><X size={12} /></button>`)).toEqual(['a11y/control-name'])
    expect(rules(`${icons}const A = ({ p }) => <button>{p ? <Loader2 /> : t('a.save')}</button>`)).toEqual(['a11y/control-name'])
    expect(rules(`${icons}const A = () => <button aria-label={t('a.close')}><X /></button>`)).toEqual([])
    expect(rules(`${icons}const A = () => <button><X /> {t('a.close')}</button>`)).toEqual([])
    expect(rules(`${icons}const A = () => <Link to="/x"><X /><span className="sr-only">{t('a.b')}</span></Link>`)).toEqual([])
  })

  it('reports a form control with no label', () => {
    expect(rules('const A = () => <input value={v} placeholder={t("a")} />')).toEqual(['a11y/input-label'])
    expect(rules('const A = () => <><label>{t("a")}</label><select value={v} /></>')).toEqual(['a11y/input-label'])
    expect(rules('const A = () => <><label htmlFor="n">{t("a")}</label><textarea id="m" /></>')).toEqual(['a11y/input-label'])
    expect(rules('const A = () => <><label htmlFor="n">{t("a")}</label><textarea id="n" /></>')).toEqual([])
    expect(rules('const A = () => <><label htmlFor={`${id}-n`}>{t("a")}</label><input id={`${id}-n`} /></>')).toEqual([])
    expect(rules('const A = () => <label>{t("a")} <input type="checkbox" /></label>')).toEqual([])
    expect(rules('const A = () => <select aria-label={t("a")} />')).toEqual([])
    expect(rules('const A = () => <input type="hidden" value="1" />')).toEqual([])
  })

  it('reports a clickable element a keyboard cannot reach or use', () => {
    expect(rules('const A = () => <div onClick={f}>{t("a")}</div>')).toEqual(['a11y/click-keyboard'])
    expect(rules('const A = () => <li onClick={f} onKeyDown={g}>{t("a")}</li>')).toEqual(['a11y/click-keyboard'])
    expect(rules('const A = () => <div role="button" tabIndex={0} onClick={f} onKeyDown={g}>{t("a")}</div>')).toEqual([])
    expect(rules('const A = () => <div role="presentation" onClick={close} />')).toEqual([])
    expect(rules('const A = () => <button onClick={f}>{t("a")}</button>')).toEqual([])
  })

  it('reports turning the i18next rule off, by name or wholesale', () => {
    expect(rules('// eslint-disable-next-line i18next/no-literal-string\nconst A = () => <p>Hi</p>')).toEqual(['text/lint-disabled'])
    expect(rules('/* eslint-disable */\nconst A = 1')).toEqual(['text/lint-disabled'])
    expect(rules('// eslint-disable-next-line @typescript-eslint/no-explicit-any\nconst A: any = 1')).toEqual([])
  })
})

describe('the views keep to the rules', () => {
  const found = checkViews(SRC)
  const counts = countByEntry(found)
  const baseline = JSON.parse(readFileSync(BASELINE, 'utf8')) as { description: string; allowlist: Record<string, number> }

  it('breaks no rule beyond the allowlist', () => {
    const over = found.filter(v => (counts[`${v.file}: ${v.rule}`] ?? 0) > (baseline.allowlist[`${v.file}: ${v.rule}`] ?? 0))
    const report = over.map(v => `${v.file}:${v.line}  ${v.rule}  ${v.text}`).join('\n')
    expect(over, `New violations of the view rules (src/test/uiRules.ts says what each rule wants; put text in en.json and es.json):\n${report}`).toEqual([])
  })

  it('keeps an allowlist that only shrinks', () => {
    const stale = Object.entries(baseline.allowlist).filter(([entry, allowed]) => (counts[entry] ?? 0) < allowed)
    if (stale.length && process.env.UPDATE_UI_RULES_BASELINE) {
      const allowlist = Object.fromEntries(Object.entries(baseline.allowlist)
        .map(([entry, allowed]) => [entry, Math.min(allowed, counts[entry] ?? 0)] as const)
        .filter(([, allowed]) => allowed > 0))
      writeFileSync(BASELINE, `${JSON.stringify({ ...baseline, allowlist }, null, 2)}\n`)
      return
    }
    const report = stale.map(([entry, allowed]) => `${entry}: allowed ${allowed}, found ${counts[entry] ?? 0}`).join('\n')
    expect(stale, `Fixed, so lower the allowlist (UPDATE_UI_RULES_BASELINE=1 npx vitest run src/uiRules.test.ts):\n${report}`).toEqual([])
  })
})
