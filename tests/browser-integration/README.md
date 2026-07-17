# Browser integration check

`pytest` never renders anything — it can confirm the API returns the right
status/JSON, but not whether the SPA actually *shows* the result to a user
(e.g. whether a failed login surfaces an error message, or silently
redirects because of an axios interceptor quirk). This directory is a
manual, opt-in tier for exactly that gap, following the same pattern as
`tests/postgres-integration/` and `tests/kea-integration/`: not part of
`pytest`, not wired into CI.

## Running it

Requires a running dev session (`./dev.sh`, from the repo root, in another
terminal) — the dev container it starts already has Playwright and
Chromium installed (see `Containerfile.dev`), so no separate setup is
needed:

```bash
podman exec drawbridge-dev node /app/tests/browser-integration/browser-check.js
```

Or against a Vite dev server started outside the container (needs
`npm install -g playwright && playwright install --with-deps chromium`
on the host first):

```bash
BASE_URL=http://localhost:5173 node tests/browser-integration/browser-check.js
```

`browser-check.js` drives a real headless Chromium: navigates to
`/login`, submits the wrong password, and asserts the `.alert-error` DaisyUI
alert actually appears with non-empty text instead of the page silently
reloading. Prints `PASS`/`FAIL` and exits non-zero on failure.
