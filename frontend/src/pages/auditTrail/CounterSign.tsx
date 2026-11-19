import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Copy, FileSignature, PenLine } from 'lucide-react'
import { errorDetail } from '../../services/api'
import { useHeadStatement, useSubmitSignature } from '../../services/auditTrail'
import { useAppStore } from '../../store/useAppStore'
import { FormActions, Panel, SavedNote, Spinner } from '../../components/ui'

/** Base64, as `openssl … | base64 -w0` writes it; the backend decides whether it verifies. */
const BASE64 = /^[A-Za-z0-9+/]+={0,2}$/

/** The documented way to sign (AUDIT_ANCHORS.md, The customer's key): a command, not prose. */
const SIGN_COMMAND = 'openssl pkeyutl -sign -inkey customer.pem -rawin -in statement.json | base64 -w0'

/**
 * Counter-signing the chain's head with the company's own key (the
 * customer_key anchor of docs/design/AUDIT_ANCHORS.md): fetch the statement,
 * sign it outside the factory with the Ed25519 key an Owner registered, paste
 * the signature. The private key never comes near this page; the backend
 * verifies the signature against the registered public keys and the chain.
 */
export function CounterSign() {
  const { t } = useTranslation()
  const companyId = useAppStore(state => state.companyId)
  const statement = useHeadStatement(companyId)
  const submit = useSubmitSignature(companyId)
  const ids = { statement: useId(), signature: useId(), hint: useId() }
  const [signature, setSignature] = useState('')
  const [copied, setCopied] = useState(false)
  const head = statement.data
  const trimmed = signature.trim()
  const shapeOk = trimmed === '' || BASE64.test(trimmed)

  return (
    <Panel title={<span className="flex items-center gap-1.5"><FileSignature size={13} aria-hidden="true" className="text-gray-400" />{t('auditTrail.signTitle')}</span>}>
      <div className="space-y-3 p-3">
        <p className="text-xs text-gray-400">{t('auditTrail.signIntro')}</p>
        <button type="button" className="btn-tool border border-surface-700" disabled={statement.isPending || companyId === null}
          onClick={() => { setCopied(false); submit.reset(); statement.mutate() }}>
          {statement.isPending ? <Spinner size={13} /> : <PenLine size={13} aria-hidden="true" />}
          {head ? t('auditTrail.refreshStatement') : t('auditTrail.getStatement')}
        </button>
        {statement.isError && <p role="alert" className="alert-error text-sm">{t('auditTrail.statementError', { message: errorDetail(statement.error) })}</p>}
        {head && (
          <form className="space-y-3" aria-label={t('auditTrail.signTitle')}
            onSubmit={event => {
              event.preventDefault()
              if (trimmed && shapeOk) submit.mutate({ head, signature: trimmed })
            }}>
            <div>
              <label htmlFor={ids.statement} className="input-label">{t('auditTrail.statementLabel', { seq: head.seq })}</label>
              <div className="flex flex-wrap gap-2">
                <textarea id={ids.statement} readOnly rows={3} value={head.statement} spellCheck={false}
                  className="input min-w-0 flex-1 font-mono text-xs" />
                <button type="button" className="btn-tool self-start border border-surface-700"
                  onClick={() => { void navigator.clipboard?.writeText(head.statement).then(() => setCopied(true), () => setCopied(false)) }}>
                  <Copy size={13} aria-hidden="true" />{t('auditTrail.copyStatement')}
                </button>
                {copied && <SavedNote>{t('auditTrail.copied')}</SavedNote>}
              </div>
              <p className="text-meta mt-1">{t('auditTrail.signHow')}</p>
              <code className="mt-1 block break-all rounded-md bg-surface-950 p-2 font-mono text-[11px] text-gray-300">
                {SIGN_COMMAND}
              </code>
            </div>
            <div>
              <label htmlFor={ids.signature} className="input-label">{t('auditTrail.signatureLabel')}</label>
              <textarea id={ids.signature} rows={2} value={signature} spellCheck={false} autoComplete="off"
                aria-invalid={!shapeOk} aria-describedby={ids.hint}
                onChange={event => setSignature(event.target.value)} className="input w-full font-mono text-xs" />
              <p id={ids.hint} className={`text-meta ${shapeOk ? '' : 'text-red-300'}`}>
                {shapeOk ? t('auditTrail.signatureHint') : t('auditTrail.signatureShape')}
              </p>
            </div>
            <FormActions>
              <button type="submit" className="btn-tool border border-surface-700" disabled={submit.isPending || !trimmed || !shapeOk}>
                {submit.isPending && <Spinner size={13} />}{t('auditTrail.submitSignature')}
              </button>
              {submit.isSuccess && <SavedNote>{t('auditTrail.signed', { seq: submit.data.seq })}</SavedNote>}
              {submit.isError && <span role="alert" className="text-xs text-red-300">{t('auditTrail.signError', { message: errorDetail(submit.error) })}</span>}
            </FormActions>
          </form>
        )}
      </div>
    </Panel>
  )
}
