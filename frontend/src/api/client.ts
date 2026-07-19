import axios from 'axios'
import type { AxiosError, AxiosInstance, AxiosRequestConfig } from 'axios'
import { ApiError } from '../types'

export interface TypedClient {
  get: <T>(url: string, config?: AxiosRequestConfig) => Promise<T>
  post: <T>(url: string, data?: unknown, config?: AxiosRequestConfig) => Promise<T>
  put: <T>(url: string, data?: unknown, config?: AxiosRequestConfig) => Promise<T>
  delete: <T>(url: string, config?: AxiosRequestConfig) => Promise<T>
}

// Attaches the standard envelope-unwrapping / 401-redirect interceptor to
// an axios instance. Shared by client.ts (/api/v1) and filesClient.ts
// (/files) since both backends speak the same {success, message, payload}
// envelope but live under different URL prefixes.
export function attachEnvelopeInterceptor(instance: AxiosInstance): void {
  instance.interceptors.response.use(
    (response) => response.data.payload ?? null,
    (error: AxiosError<{ message?: string; error?: string }>) => {
      const skip401Redirect = error.config?.skip401Redirect
      if (error.response?.status === 401 && !skip401Redirect) {
        window.location.href = '/login'
        return new Promise(() => {}) // never resolves — page is navigating away
      }

      const envelope = error.response?.data
      const message = envelope?.message ?? error.message
      return Promise.reject(new ApiError(message, envelope?.error ?? null, error.response?.status ?? null))
    },
  )
}

// The interceptor above changes what get/post/put/delete actually resolve
// to (the unwrapped payload, not an AxiosResponse) — stock axios types have
// no way to know that, so wrap the instance in typed passthroughs instead of
// exporting it directly. Shared by client.ts and filesClient.ts so the
// wrapper isn't duplicated per instance.
export function wrapTyped(instance: AxiosInstance): TypedClient {
  return {
    get: <T>(url: string, config?: AxiosRequestConfig) => instance.get(url, config) as unknown as Promise<T>,
    post: <T>(url: string, data?: unknown, config?: AxiosRequestConfig) =>
      instance.post(url, data, config) as unknown as Promise<T>,
    put: <T>(url: string, data?: unknown, config?: AxiosRequestConfig) =>
      instance.put(url, data, config) as unknown as Promise<T>,
    delete: <T>(url: string, config?: AxiosRequestConfig) => instance.delete(url, config) as unknown as Promise<T>,
  }
}

// Single configured axios instance — components never import axios
// directly, only Pinia stores call through this (see docs/frontend.md).
const client = axios.create({
  baseURL: '/api/v1',
  withCredentials: true,
})

attachEnvelopeInterceptor(client)

export default wrapTyped(client)
