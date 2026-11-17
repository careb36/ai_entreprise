/**
 * The checks behind `src/uiRules.test.ts`: user-facing text and accessibility
 * rules the i18next lint rule (`eslint.config.mjs`) cannot see, read from the
 * TypeScript syntax tree of every view in `pages/` and `components/`.
 *
 * - `text/template`: a template literal shown as JSX text, or given to an
 *   attribute a person reads or hears, with words outside its `${…}`.
 * - `text/attribute`: a prose literal given to an attribute the lint rule does
 *   not check (a component's own `heading`, `emptyText`, `aria-valuetext`…).
 * - `a11y/img-alt`: an `<img>` without `alt`.
 * - `a11y/control-name`: a `<button>`, `<a>` or `<Link>` with no text and no
 *   `aria-label`, `aria-labelledby` or `title` (an icon-only control).
 * - `a11y/input-label`: an `<input>`, `<select>` or `<textarea>` with no
 *   label: none wrapping it, no `htmlFor` naming its `id`, no `aria-label`,
 *   `aria-labelledby` or `title`.
 * - `a11y/click-keyboard`: a `div`, `span`, `li`, … with `onClick` that a
 *   keyboard cannot reach or use (no key handler, or neither `role` nor
 *   `tabIndex`).
 * - `text/lint-disabled`: an `eslint-disable` comment that turns the
 *   i18next rule off (by name, or every rule at once).
 *
 * Test support only: it imports `typescript` and `node:fs`, never shipped.
 */
import { readdirSync, readFileSync } from 'node:fs'
import { join, relative } from 'node:path'
import ts from 'typescript'

export interface Violation {
  file: string
  rule: string
  line: number
  text: string
}

// The attributes the i18next lint rule already checks (eslint.config.mjs).
const LINTED_ATTRIBUTES = new Set(['title', 'alt', 'placeholder', 'aria-label', 'aria-description', 'label'])
// Read or heard by a person, beyond the linted ones.
const SPOKEN_ATTRIBUTES = new Set([...LINTED_ATTRIBUTES, 'aria-valuetext', 'aria-roledescription', 'aria-placeholder'])
// Attributes whose value is never prose a person reads.
const TECHNICAL_ATTRIBUTES = new Set([
  'className', 'style', 'key', 'id', 'ref', 'to', 'href', 'src', 'type', 'role', 'htmlFor', 'name', 'value',
  'defaultValue', 'rel', 'target', 'method', 'action', 'autoComplete', 'inputMode', 'accept', 'pattern', 'lang',
  'dir', 'form', 'list', 'step', 'min', 'max', 'tabIndex', 'width', 'height', 'size', 'variant', 'tone', 'icon',
  'd', 'viewBox', 'fill', 'stroke', 'points', 'transform', 'xmlns', 'textAnchor', 'dominantBaseline', 'fontFamily',
  'strokeDasharray', 'strokeLinecap', 'strokeLinejoin', 'preserveAspectRatio', 'markerEnd', 'clipPath', 'mask',
  'spellCheck', 'download', 'sizes', 'srcSet', 'loading', 'decoding', 'crossOrigin', 'encType', 'wrap', 'scope',
  'colSpan', 'rowSpan', 'headers', 'shape', 'coords', 'media', 'charSet', 'content', 'httpEquiv', 'property',
  'queryKey', 'path', 'element', 'language', 'kind', 'mode', 'status', 'section', 'tab', 'align', 'side',
])
const TECHNICAL_PREFIXES = ['data-', 'on', 'aria-']
const NATIVE_INTERACTIVE = new Set(['button', 'a', 'input', 'select', 'textarea', 'summary', 'label', 'option', 'details', 'form'])
const LABELLED_BY = ['aria-label', 'aria-labelledby', 'title']
const PROSE = /[A-Za-z]{2,}[,.:;!?'’]?\s+[A-Za-z]{2,}|^[A-Z][a-z]{2,}$/
const WORD = /[A-Za-z]{2,}/

type Jsx = ts.JsxElement | ts.JsxSelfClosingElement

function sourceFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap(entry => {
    const path = join(dir, entry.name)
    if (entry.isDirectory()) return entry.name === '__tests__' ? [] : sourceFiles(path)
    return /\.tsx$/.test(entry.name) && !/\.test\.tsx$/.test(entry.name) ? [path] : []
  })
}

function opening(node: Jsx): ts.JsxOpeningLikeElement {
  return ts.isJsxElement(node) ? node.openingElement : node
}

function tagName(node: Jsx): string {
  return opening(node).tagName.getText()
}

function attributes(node: Jsx): Map<string, ts.JsxAttribute> {
  const map = new Map<string, ts.JsxAttribute>()
  for (const attr of opening(node).attributes.properties) {
    if (ts.isJsxAttribute(attr)) map.set(attr.name.getText(), attr)
  }
  return map
}

function hasSpread(node: Jsx): boolean {
  return opening(node).attributes.properties.some(ts.isJsxSpreadAttribute)
}

function literalValue(attr: ts.JsxAttribute | undefined): string | null {
  const init = attr?.initializer
  if (!init) return null
  if (ts.isStringLiteral(init)) return init.text
  if (ts.isJsxExpression(init) && init.expression && ts.isStringLiteralLike(init.expression)) return init.expression.text
  return null
}

function isTranslateCall(node: ts.Node): boolean {
  if (!ts.isCallExpression(node)) return false
  const callee = node.expression.getText()
  return callee === 't' || callee === 'i18n.t' || callee.endsWith('.t')
}

const COMPARISONS = [
  ts.SyntaxKind.EqualsEqualsEqualsToken, ts.SyntaxKind.ExclamationEqualsEqualsToken,
  ts.SyntaxKind.EqualsEqualsToken, ts.SyntaxKind.ExclamationEqualsToken,
]

function templateText(node: ts.TemplateExpression): string[] {
  return [node.head.text, ...node.templateSpans.map(s => s.literal.text)]
}

function templateHasWords(node: ts.TemplateExpression): boolean {
  return templateText(node).some(part => WORD.test(part))
}

/**
 * The literals in an expression that *matches*, skipping what is never shown:
 * translation calls (their keys and English defaults), the arguments of other
 * calls, comparisons, keys of an index or an object, and nested JSX (checked
 * as an element of its own).
 */
function literalsIn(expr: ts.Node, matches: (node: ts.StringLiteralLike | ts.TemplateExpression) => boolean): ts.Node[] {
  const found: ts.Node[] = []
  const visit = (node: ts.Node) => {
    if (isTranslateCall(node) || ts.isJsxElement(node) || ts.isJsxSelfClosingElement(node) || ts.isJsxFragment(node)) return
    if (ts.isBinaryExpression(node) && COMPARISONS.includes(node.operatorToken.kind)) return
    if (ts.isElementAccessExpression(node)) { visit(node.expression); return }
    if (ts.isCallExpression(node)) { visit(node.expression); return }
    if (ts.isPropertyAssignment(node)) { visit(node.initializer); return }
    if (ts.isStringLiteralLike(node) || ts.isTemplateExpression(node)) {
      if (matches(node)) found.push(node)
      if (ts.isStringLiteralLike(node)) return
    }
    ts.forEachChild(node, visit)
  }
  visit(expr)
  return found
}

const proseLiterals = (expr: ts.Node) => literalsIn(expr, node =>
  PROSE.test((ts.isTemplateExpression(node) ? templateText(node).join(' ') : node.text).trim()))

// A template with words is text the lint rule cannot see; a plain literal it
// already reports.
const wordyTemplates = (expr: ts.Node) => literalsIn(expr, node => ts.isTemplateExpression(node) && templateHasWords(node))

/** Whether an expression rendered as a child gives text: JSX is looked into, the rest is assumed text. */
function expressionHasText(expr: ts.Expression, icons: Set<string>): boolean {
  if (ts.isParenthesizedExpression(expr)) return expressionHasText(expr.expression, icons)
  if (ts.isConditionalExpression(expr)) return expressionHasText(expr.whenTrue, icons) && expressionHasText(expr.whenFalse, icons)
  if (ts.isBinaryExpression(expr) && expr.operatorToken.kind === ts.SyntaxKind.AmpersandAmpersandToken) return expressionHasText(expr.right, icons)
  if (expr.kind === ts.SyntaxKind.NullKeyword || (ts.isIdentifier(expr) && expr.text === 'undefined')) return false
  if (ts.isJsxElement(expr) || ts.isJsxSelfClosingElement(expr) || ts.isJsxFragment(expr)) {
    return hasText(ts.factory.createNodeArray<ts.JsxChild>([expr]), icons)
  }
  return true
}

/** Whether the element's children give it a text name. */
function hasText(children: ts.NodeArray<ts.JsxChild>, icons: Set<string>): boolean {
  return children.some(child => {
    if (ts.isJsxText(child)) return child.text.trim() !== ''
    if (ts.isJsxExpression(child)) return child.expression !== undefined && expressionHasText(child.expression, icons)
    if (ts.isJsxFragment(child)) return hasText(child.children, icons)
    const tag = tagName(child)
    if (icons.has(tag) || tag === 'svg') return false
    if (/^[A-Z]/.test(tag) || tag.includes('.')) return true
    const attrs = attributes(child)
    if (LABELLED_BY.some(a => attrs.has(a)) || attrs.has('alt')) return true
    return ts.isJsxElement(child) && hasText(child.children, icons)
  })
}

export function checkSource(file: string, source: string): Violation[] {
  const tree = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  const out: Violation[] = []
  const report = (rule: string, node: ts.Node) => {
    const line = tree.getLineAndCharacterOfPosition(node.getStart(tree)).line + 1
    out.push({ file, rule, line, text: node.getText(tree).replace(/\s+/g, ' ').slice(0, 100) })
  }

  const icons = new Set<string>()
  const htmlFor = { literals: new Set<string>(), dynamic: false }
  tree.statements.forEach(statement => {
    if (ts.isImportDeclaration(statement) && (statement.moduleSpecifier as ts.StringLiteral).text === 'lucide-react') {
      const bindings = statement.importClause?.namedBindings
      if (bindings && ts.isNamedImports(bindings)) bindings.elements.forEach(e => icons.add(e.name.text))
    }
  })
  const collect = (node: ts.Node) => {
    if (ts.isJsxAttribute(node) && node.name.getText() === 'htmlFor') {
      const value = literalValue(node)
      if (value === null) htmlFor.dynamic = true
      else htmlFor.literals.add(value)
    }
    ts.forEachChild(node, collect)
  }
  collect(tree)

  const inLabel = (node: ts.Node) => {
    for (let p = node.parent; p; p = p.parent) {
      if (ts.isJsxElement(p) && tagName(p) === 'label') return true
    }
    return false
  }

  const checkElement = (node: Jsx) => {
    const tag = tagName(node)
    const attrs = attributes(node)
    const spread = hasSpread(node)
    const named = LABELLED_BY.some(a => attrs.has(a))

    for (const [name, attr] of attrs) {
      const init = attr.initializer
      if (!init) continue
      const expr = ts.isJsxExpression(init) ? init.expression : init
      if (!expr) continue
      if (SPOKEN_ATTRIBUTES.has(name)) {
        wordyTemplates(expr).forEach(n => report('text/template', n))
        if (!LINTED_ATTRIBUTES.has(name)) {
          proseLiterals(expr).filter(n => !ts.isTemplateExpression(n)).forEach(n => report('text/attribute', n))
        }
        continue
      }
      if (TECHNICAL_ATTRIBUTES.has(name) || TECHNICAL_PREFIXES.some(p => name.startsWith(p))) continue
      if (/^[a-z]/.test(tag) && !tag.includes('.')) continue // a DOM attribute outside the lists is not prose
      proseLiterals(expr).forEach(n => report('text/attribute', n))
    }

    if (tag === 'img' && !attrs.has('alt') && !spread) report('a11y/img-alt', opening(node))

    if ((tag === 'button' || tag === 'a' || tag === 'Link') && !named && !spread) {
      const children = ts.isJsxElement(node) ? node.children : ts.factory.createNodeArray<ts.JsxChild>()
      if (!hasText(children, icons)) report('a11y/control-name', opening(node))
    }

    if ((tag === 'input' || tag === 'select' || tag === 'textarea') && !named && !spread && !inLabel(node)) {
      const type = literalValue(attrs.get('type'))
      const exempt = type !== null && ['hidden', 'submit', 'button', 'reset'].includes(type)
      const id = attrs.get('id')
      const idValue = literalValue(id)
      const labelled = id !== undefined && (idValue === null ? htmlFor.dynamic || htmlFor.literals.size > 0 : htmlFor.literals.has(idValue))
      if (!exempt && !labelled) report('a11y/input-label', opening(node))
    }

    if (/^[a-z]/.test(tag) && !NATIVE_INTERACTIVE.has(tag) && attrs.has('onClick') && !spread) {
      const role = literalValue(attrs.get('role'))
      const hidden = literalValue(attrs.get('aria-hidden')) === 'true' || attrs.get('aria-hidden')?.initializer === undefined && attrs.has('aria-hidden')
      const decorative = hidden || role === 'presentation' || role === 'none'
      const keys = ['onKeyDown', 'onKeyUp', 'onKeyPress'].some(k => attrs.has(k))
      const reachable = attrs.has('tabIndex') || attrs.has('role')
      if (!decorative && !(keys && reachable)) report('a11y/click-keyboard', opening(node))
    }
  }

  const visit = (node: ts.Node) => {
    if (ts.isJsxElement(node) || ts.isJsxSelfClosingElement(node)) checkElement(node)
    if (ts.isJsxExpression(node) && node.expression && (ts.isJsxElement(node.parent) || ts.isJsxFragment(node.parent))) {
      wordyTemplates(node.expression).forEach(n => report('text/template', n))
    }
    ts.forEachChild(node, visit)
  }
  visit(tree)

  // Turning the lint rule off hides everything it would have reported.
  source.split('\n').forEach((text, index) => {
    const match = text.match(/eslint-disable(?:-next-line|-line)?(.*)$/)
    if (!match) return
    const rules = match[1].replace(/\*\/.*$/, '').replace(/--.*$/, '').trim()
    if (rules === '' || rules.includes('i18next/no-literal-string')) {
      out.push({ file, rule: 'text/lint-disabled', line: index + 1, text: text.trim().slice(0, 100) })
    }
  })
  return out
}

/** Every violation in the views under `src` (`pages/` and `components/`). */
export function checkViews(src: string): Violation[] {
  return ['pages', 'components'].flatMap(folder => sourceFiles(join(src, folder)))
    .flatMap(path => checkSource(relative(src, path).split('\\').join('/'), readFileSync(path, 'utf8')))
}

/** The violations counted by `file: rule`, the allowlist's shape. */
export function countByEntry(violations: Violation[]): Record<string, number> {
  const counts: Record<string, number> = {}
  for (const v of violations) counts[`${v.file}: ${v.rule}`] = (counts[`${v.file}: ${v.rule}`] ?? 0) + 1
  return Object.fromEntries(Object.entries(counts).sort(([a], [b]) => a.localeCompare(b)))
}
