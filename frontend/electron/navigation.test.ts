// @vitest-environment node
import { describe, expect, it } from 'vitest'
import { mainWindowNavigation, newWindowRequest, ssoWindowNavigation } from './navigation'

const local = 'http://127.0.0.1:4000'

describe('main window navigation', () => {
  it('stays on the local origin', () => {
    expect(mainWindowNavigation(`${local}/evidence/1`, local)).toEqual({ kind: 'allow' })
    expect(mainWindowNavigation(`${local}/login/sso?code=c`, local)).toEqual({ kind: 'allow' })
  })

  it('starts single sign-on in its own window', () => {
    expect(mainWindowNavigation(`${local}/v1/auth/sso/acme/start`, local)).toEqual({ kind: 'sso', url: `${local}/v1/auth/sso/acme/start` })
  })

  it('opens other web links in the system browser and nothing else', () => {
    expect(mainWindowNavigation('https://github.com/x/y', local)).toEqual({ kind: 'external', url: 'https://github.com/x/y' })
    // Another local port is another origin.
    expect(mainWindowNavigation('http://127.0.0.1:5173/', local)).toEqual({ kind: 'external', url: 'http://127.0.0.1:5173/' })
    expect(mainWindowNavigation('file:///etc/passwd', local)).toEqual({ kind: 'deny' })
    expect(mainWindowNavigation('javascript:alert(1)', local)).toEqual({ kind: 'deny' })
    expect(mainWindowNavigation('not a url', local)).toEqual({ kind: 'deny' })
  })
})

describe('new windows', () => {
  it('never opens an Electron window; web links go to the system browser', () => {
    expect(newWindowRequest('https://github.com/x/y', local)).toEqual({ kind: 'external', url: 'https://github.com/x/y' })
    expect(newWindowRequest(`${local}/evidence`, local)).toEqual({ kind: 'deny' })
    expect(newWindowRequest('file:///etc/passwd', local)).toEqual({ kind: 'deny' })
  })
})

describe('single sign-on window', () => {
  it('goes through the local start route and the provider\'s pages', () => {
    expect(ssoWindowNavigation(`${local}/v1/auth/sso/acme/start`, local)).toEqual({ kind: 'allow' })
    expect(ssoWindowNavigation('https://idp.example.com/authorize?state=s', local)).toEqual({ kind: 'allow' })
  })

  it('takes the provider\'s redirect to the backend callback through the local origin', () => {
    expect(ssoWindowNavigation('https://api.example.com/v1/auth/sso/callback?state=s&code=c', local))
      .toEqual({ kind: 'load', url: `${local}/v1/auth/sso/callback?state=s&code=c` })
    expect(ssoWindowNavigation(`${local}/v1/auth/sso/callback?state=s&code=c`, local)).toEqual({ kind: 'allow' })
  })

  it('hands /login/sso back to the main window', () => {
    expect(ssoWindowNavigation(`${local}/login/sso?code=handoff`, local)).toEqual({ kind: 'finish', url: `${local}/login/sso?code=handoff` })
  })

  it('refuses anything but the web', () => {
    expect(ssoWindowNavigation('file:///etc/passwd', local)).toEqual({ kind: 'deny' })
    expect(ssoWindowNavigation('fabrique://x', local)).toEqual({ kind: 'deny' })
  })
})
