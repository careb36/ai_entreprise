#!/usr/bin/env node
// `npm run test:changed`: the vitest tests that import a file changed against
// a base ref (committed since the merge base, staged, unstaged or untracked).
//
// The base is TEST_CHANGED_BASE, else origin/main. When that ref does not
// exist (a fresh clone without the remote, a CI checkout of one commit), it
// falls back to `--changed` with no ref: the uncommitted changes only, and
// says so. Arguments after `--` go to vitest.
import { spawnSync } from 'node:child_process'

const base = process.env.TEST_CHANGED_BASE || 'origin/main'
const known = spawnSync('git', ['rev-parse', '--verify', '--quiet', `${base}^{commit}`], { stdio: 'ignore' })
const changed = known.status === 0 ? ['--changed', base] : ['--changed']
if (known.status !== 0) {
  console.warn(`test:changed: ${base} is not a known ref; running the tests for the uncommitted changes only.`)
}

const vitest = process.platform === 'win32' ? 'vitest.cmd' : 'vitest'
const result = spawnSync(vitest, ['run', ...changed, ...process.argv.slice(2)], {
  stdio: 'inherit',
  shell: process.platform === 'win32',
})
process.exit(result.status ?? 1)
