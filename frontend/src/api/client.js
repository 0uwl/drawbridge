import axios from 'axios'

// Attaches the standard envelope-unwrapping / 401-redirect interceptor to
// an axios instance. Shared by client.js (/api/v1) and filesClient.js
// (/files) since both backends speak the same {success, message, payload}
// envelope but live under different URL prefixes.
export function attachEnvelopeInterceptor(instance) {
  instance.interceptors.response.use(
    (response) => response.data.payload ?? null,
    (error) => {
      const skip401Redirect = error.config?.skip401Redirect
      if (error.response?.status === 401 && !skip401Redirect) {
        window.location.href = '/login'
        return new Promise(() => {}) // never resolves — page is navigating away
      }

      const envelope = error.response?.data
      const message = envelope?.message ?? error.message
      const wrapped = new Error(message)
      wrapped.code = envelope?.error ?? null
      wrapped.status = error.response?.status ?? null
      return Promise.reject(wrapped)
    },
  )
}

// Single configured axios instance — components never import axios
// directly, only Pinia stores call through this (see docs/frontend.md).
const client = axios.create({
  baseURL: '/api/v1',
  withCredentials: true,
})

attachEnvelopeInterceptor(client)

export default client
