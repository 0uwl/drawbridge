#!/usr/bin/env node
// Headless-Chromium smoke test for the login page, run against a live
// dev.sh session. Not part of `pytest` (nothing here touches the backend
// directly) — a manual/CI check for frontend behavior that only shows up
// in a real browser, e.g. whether a failed login actually surfaces an
// error message instead of silently bouncing (see auth.ts's login()).
//
// CommonJS (not .mjs): Playwright is installed globally, resolved via
// NODE_PATH — Node's ESM loader ignores NODE_PATH entirely, only require()
// honors it, so this has to stay CJS rather than `import`.
//
// Usage (while `dev.sh` is running in another terminal):
//   npm install -g playwright && playwright install --with-deps chromium
//   BASE_URL=http://localhost:5173 node tests/browser-integration/browser-check.js
//
// Already available inside the dev container built from Containerfile.dev:
//   podman exec drawbridge-dev node /app/tests/browser-integration/browser-check.js

const { chromium } = require('playwright')

const baseUrl = process.env.BASE_URL || 'http://localhost:5173'

async function main() {
  const browser = await chromium.launch()
  try {
    const page = await browser.newPage()
    await page.goto(`${baseUrl}/login`)

    await page.fill('input[type="text"]', 'admin')
    await page.fill('input[type="password"]', 'definitely-the-wrong-password')
    await page.click('button[type="submit"]')

    const alert = page.locator('.alert-error')
    await alert.waitFor({ state: 'visible', timeout: 5000 })
    const text = await alert.textContent()

    if (!text || !text.trim()) {
      throw new Error('Error alert appeared but was empty')
    }
    if (page.url() !== `${baseUrl}/login`) {
      throw new Error(`Expected to stay on /login, ended up at ${page.url()}`)
    }

    console.log(`PASS: login error shown — "${text.trim()}"`)
  } finally {
    await browser.close()
  }
}

main().catch((err) => {
  console.error(`FAIL: ${err.message}`)
  process.exit(1)
})
