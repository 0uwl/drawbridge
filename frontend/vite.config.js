import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'

// Matches the backend's own DRAWBRIDGE_PORT (see docs/deployment.md) —
// dev.sh exports it before starting both processes, so this picks it up
// automatically; only needs setting by hand if running Vite standalone
// against a backend started on a non-default port.
const backendTarget = `http://127.0.0.1:${process.env.DRAWBRIDGE_PORT || '8080'}`

// Build output goes straight into the Flask package's static folder so:
//   - `npm run build` always leaves drawbridge/static ready to serve, for
//     local "build once and check it" testing without a container, and
//   - the Containerfile's frontend-build stage copies from the same path
//     it would land in locally (see docs/frontend.md).
export default defineConfig({
  plugins: [vue(), tailwindcss()],
  build: {
    outDir: '../drawbridge/static',
    emptyOutDir: true,
  },
  server: {
    proxy: {
      '/api': backendTarget,
      '/files': backendTarget,
      '/health': backendTarget,
    },
  },
})
