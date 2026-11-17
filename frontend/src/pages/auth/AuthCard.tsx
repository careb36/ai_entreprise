import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

/**
 * The frame of the sign-in pages (Login, Register, SsoComplete): the product
 * name above one calm card in the board's surfaces (`panel`), centred in the
 * page, with the page's one `<h1>` and an optional line under the card.
 */
export function AuthCard({ title, children, footer }: {
  title?: ReactNode
  children: ReactNode
  footer?: ReactNode
}) {
  const { t } = useTranslation()
  return (
    <div className="flex min-h-full justify-center px-4 py-10 sm:items-center sm:py-16">
      <div className="w-full max-w-sm">
        <p className="mb-5 flex items-center justify-center gap-2 text-[15px] font-semibold tracking-tight text-gray-100">
          <img src="/fabrique-icon.svg" alt="" width={28} height={28} className="rounded-md" />
          {t('auth.productName', { defaultValue: 'La Fabrique' })}
        </p>
        <div className="panel p-5 shadow-card sm:p-6">
          {title && <h1 className="page-title mb-5">{title}</h1>}
          {children}
        </div>
        {footer && <p className="mt-4 text-center text-[13px] text-gray-400">{footer}</p>}
      </div>
    </div>
  )
}

/**
 * The text of a sign-in field's label. The `<label>` wraps the control in the
 * page itself, where the view rules (src/test/uiRules.ts) can see it.
 */
export const AUTH_LABEL = 'mb-1.5 block text-xs font-medium text-gray-300'

/** The link under the card to the other sign-in page. */
export const AUTH_LINK = 'rounded-sm font-medium text-brand-300 hover:text-brand-200 underline-offset-2 hover:underline'
