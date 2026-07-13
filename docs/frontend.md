# Frontend

Drawbridge's management UI is a Vue 3 + Vite single-page app in `frontend/`,
built at image-build time and baked into the same container as the Flask
backend — there is no separate frontend server, port, or process in
production. It talks to the existing JSON API documented in
[api.md](api.md) and sits behind the same `@login_required` sessions
described in [authentication.md](authentication.md).

## Layout

```
frontend/
├── index.html          ← Vite entry HTML
├── package.json
├── vite.config.js       ← build output path + dev-server proxy (see below)
├── public/
│   └── favicon.svg
└── src/
    ├── main.js          ← mounts App, installs Pinia + Vue Router
    ├── App.vue          ← navbar shell + <router-view/>
    ├── style.css        ← Tailwind/DaisyUI entry point (see Styling below)
    ├── api/
    │   └── client.js    ← single configured axios instance
    ├── stores/          ← one Pinia store per domain (auth, devices, log, users, settings)
    ├── router/
    │   └── index.js     ← routes + the auth navigation guard
    └── views/           ← Login, Devices, Log, Settings
```

`frontend/node_modules/` and `frontend/dist/` are gitignored and
dockerignored — never commit installed packages or build output.

## Styling

The UI is styled with **Tailwind CSS v4** and **DaisyUI v5** components
(`table`, `btn`, `modal`, `navbar`, `alert`, `badge`, `form-control`, etc.).
Setup uses Tailwind v4's CSS-first configuration — no `tailwind.config.js`
or `postcss.config.js`:

- `vite.config.js` adds the `@tailwindcss/vite` plugin alongside `vue()`.
- `src/style.css` is just:
  ```css
  @import "tailwindcss";
  @plugin "daisyui";
  ```

API access stays centralized in the Pinia stores under `stores/` —
components call store actions and read store state, never import `axios`
directly. See `alpha.md` step 8 for the store/view breakdown.

## Why one container, no separate frontend server

The SPA is just static HTML/CSS/JS once built — there's no Node.js runtime
needed to *serve* it, only to *build* it. So:

- The Containerfile's first stage builds the frontend with Node and produces
  plain static files.
- Those files get copied into the Flask package's `static/` directory in
  the final image.
- Gunicorn (already running the Flask app — see [deployment.md](deployment.md))
  serves both the API and the SPA's static assets from the same process and
  port. No nginx, no second container, no extra port to publish or secure.

This matches the existing single-container, rootless-Podman, read-only-root
deployment model: the built assets are baked into the image like the rest
of the app code, not bind-mounted, and don't change unless the image is
rebuilt.

## Build output path

`vite.config.js` sets `build.outDir` to `../drawbridge/static` (relative to
`frontend/`, i.e. the Flask package's static folder) with `emptyOutDir:
true`. This one setting is what makes both of the following true without
any extra plumbing:

- Running `npm run build` locally always leaves `drawbridge/static/` ready
  to serve immediately — useful for checking the real build without
  spinning up a container.
- The Containerfile's frontend-build stage produces output at the exact
  path the final stage's `COPY --from=frontend-build` instruction expects.

## Flask integration (`drawbridge/main.py`)

`create_app()` passes `static_folder=None` to the `Flask()` constructor.
This disables Flask's *implicit* static route — if left enabled, it would
register its own rule on the same `/<path:filename>` URL pattern as the
catch-all route below, and having two rules competing for the same pattern
is exactly the kind of ambiguity worth avoiding outright rather than relying
on Werkzeug's tie-breaking.

Instead, a `FRONTEND_DIST` constant points directly at
`drawbridge/static/`, and a catch-all route serves from it:

```python
@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_frontend(path):
    target = FRONTEND_DIST / path
    if path and target.is_file():
        return send_from_directory(FRONTEND_DIST, path)
    return send_from_directory(FRONTEND_DIST, 'index.html')
```

- A request for a real built file (e.g. `/assets/index-abc123.js`) serves
  that file directly.
- Anything else (e.g. `/devices`, a client-side route with no matching
  file) falls back to `index.html`, so a hard refresh on a deep SPA route
  still works once client-side routing is added.
- This route is registered last in `create_app()`, but registration order
  doesn't actually matter: Werkzeug sorts routes by specificity, so a
  literal/blueprint route like `/api/provision-request` always matches ahead of
  the `/<path:path>` catch-all regardless of where each is added.
- If `drawbridge/static/` doesn't exist (frontend never built — the normal
  state during backend-only local dev, see below), requests here 404. That's
  expected and harmless; you're not meant to hit Flask directly for the UI
  in that mode.

## Development workflow

Two distinct workflows, depending on what you're changing:

**Frontend work (the common case) — run both servers separately:**

```bash
# terminal 1: the real backend
flask run --port ${DRAWBRIDGE_PORT:-8080}

# terminal 2: the frontend, with hot module reload
cd frontend
npm install   # first time only
npm run dev
```

Browse `http://localhost:5173` (Vite's dev server), not the backend port.
Vite's `server.proxy` config forwards `/api`, `/files`, and `/health`
requests to `http://127.0.0.1:$DRAWBRIDGE_PORT` (default `8080` if unset —
see [deployment.md](deployment.md)), so the SPA calls the real Flask
backend while you get instant HMR for `.vue` component changes. `dev.sh`
exports `DRAWBRIDGE_PORT` before starting both processes, so this matches
automatically when both are started that way; set it by hand in each
terminal above if running them separately on a non-default port.

**Checking the fully-baked integration — build once, serve through Flask:**

```bash
cd frontend
npm run build      # writes straight into ../drawbridge/static
cd ..
flask run --port ${DRAWBRIDGE_PORT:-8080}
```

Browse `http://localhost:$DRAWBRIDGE_PORT` directly. This is the same code path
production uses (`serve_frontend` above), just without the container —
useful for confirming the catch-all route and built asset paths behave
before doing a full container build.

## Container build

The Containerfile's first stage builds the frontend; the final stage copies
the result in:

```dockerfile
FROM node:22-slim AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci
COPY frontend/ .
RUN npm run build

FROM python:3.12-slim
...
COPY . .
COPY --from=frontend-build /drawbridge/static ./drawbridge/static
...
```

`npm ci` (not `npm install`) is used in the build stage for a reproducible
install from the lockfile. The final image never has Node.js in it — only
the static output survives into the runtime stage.
