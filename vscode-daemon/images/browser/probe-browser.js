#!/usr/bin/env node
// Is there a browser Playwright can launch here? One answer, as a JSON line.
//
// Run at build time by the browser sandbox image (Dockerfile) and, before QA's
// smoke tests, by the workspace daemon (vscode-daemon/browser_probe.py, which
// passes this file to `node -e` in the project's folder or the application
// check's container). It launches the browser headless the way a Playwright
// test does, opens a page, takes a screenshot (a real paint) and reads the
// page back, then prints one line:
//
//   fabrique-browser-probe: {"available":true,"playwright":"1.64.1",...}
//
// and exits 0, or prints the line with "available":false and the reason and
// exits 1. The daemon records it as its observation (`browser_probe`).
//
// Which Playwright: FABRIQUE_PLAYWRIGHT_DIR (set by the browser sandbox image
// to its own pinned copy, "from":"image"), else the project's own, resolved
// from the current folder ("from":"project").
//
// Adapted from AixleHQ/flow, docker/base/probe-browser.js at commit f9efa22
// (https://github.com/AixleHQ/flow), Copyright 2026 Dualboot Partners, LLC,
// licensed under the Apache License, Version 2.0
// (http://www.apache.org/licenses/LICENSE-2.0): the probe that launches the
// browser exactly the way its users do (navigate, screenshot, evaluate) under
// an overall watchdog, failing loudly with the reason. Changed here: Playwright
// is driven directly instead of through the MCP server over stdio, the WebGL
// step is dropped, and the outcome is a machine-readable line.

'use strict';

const path = require('path');
const { createRequire } = require('module');

const MARK = 'fabrique-browser-probe: ';
const OVERALL_TIMEOUT_MS = Number(process.env.PROBE_TIMEOUT_MS || 60000);
const pinned = (process.env.FABRIQUE_PLAYWRIGHT_DIR || '').trim();
const from = pinned ? 'image' : 'project';
const requireFrom = createRequire(path.join(pinned || process.cwd(), 'noop.js'));

let browser = null;

const report = (outcome) => {
  process.stdout.write(MARK + JSON.stringify({ from, ...outcome }) + '\n');
};

// The reason kept on the line. Playwright's error opens with the browser's
// launch command and its flags (several hundred characters) and ends with what
// the browser said before it closed: the launch line is shortened and, past
// the limit, the start and the end are kept, so the cause is not cut off.
const REASON_MAX = 500;
const REASON_HEAD = 160;
const GAP = '\n...\n';
const condense = (reason) => {
  const text = String(reason)
    .split('\n')
    .map((line) => (line.startsWith('<launching>') ? '<launching> (its flags left out)' : line))
    .join('\n');
  if (text.length <= REASON_MAX) return text;
  return text.slice(0, REASON_HEAD) + GAP + text.slice(-(REASON_MAX - REASON_HEAD - GAP.length));
};

const fail = (reason) => {
  report({ available: false, reason: condense(reason) });
  const done = () => process.exit(1);
  if (browser) browser.close().then(done, done);
  else done();
};

let watchdog = null;

const load = () => {
  for (const name of ['playwright', '@playwright/test', 'playwright-core']) {
    try {
      const module = requireFrom(name);
      if (module && module.chromium) {
        let version = null;
        try {
          version = requireFrom(`${name}/package.json`).version;
        } catch (e) {
          version = null;
        }
        return { module, name, version };
      }
    } catch (e) {
      // Not this one; try the next name.
    }
  }
  return null;
};

const probe = async () => {
  watchdog = setTimeout(
    () => fail(`the browser did not answer within ${OVERALL_TIMEOUT_MS} ms`),
    OVERALL_TIMEOUT_MS,
  );
  const found = load();
  if (!found) {
    fail(
      from === 'image'
        ? `no Playwright package in ${pinned}`
        : 'the Playwright package is not installed for this project',
    );
    return;
  }
  const { chromium } = found.module;
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  // 1. The browser launches and navigates.
  await page.goto('data:text/html,<h1 style="font-family:sans-serif">probe</h1>');
  // 2. Rendering works: a screenshot forces a real paint.
  const shot = await page.screenshot();
  if (!shot || shot.length < 32) throw new Error('the screenshot came back empty');
  // 3. The page can be read back.
  const text = await page.evaluate(() => document.body.innerText);
  if (!String(text).includes('probe')) throw new Error(`the page read back ${JSON.stringify(text)}`);
  const version = browser.version();
  await browser.close();
  browser = null;
  clearTimeout(watchdog);
  report({
    available: true,
    package: found.name,
    playwright: found.version,
    browser: `chromium ${version}`,
  });
  process.exit(0);
};

// FABRIQUE_PROBE_LIBRARY=1 loads the helpers without probing (the tests); the
// daemon and the image run the file as a script (`node -e`, `node file`).
if (process.env.FABRIQUE_PROBE_LIBRARY === '1') {
  module.exports = { condense, REASON_MAX };
} else {
  probe().catch((e) => fail((e && e.message) || e));
}
