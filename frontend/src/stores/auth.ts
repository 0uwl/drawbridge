import { defineStore } from 'pinia'
import client from '../api/client'
import type { AuthUser, LoginResponse } from '../types'

interface AuthState {
  currentUser: AuthUser | null
  pendingReset: string | null
  loading: boolean
  error: string | null
}

export const useAuthStore = defineStore('auth', {
  state: (): AuthState => ({
    currentUser: null,
    // Username pending a forced password reset (see docs/authentication.md,
    // "Bootstrap admin password sources") — set when login() succeeds but
    // the server withheld a session because must_reset_password is set.
    pendingReset: null,
    loading: false,
    error: null,
  }),
  actions: {
    async login(username: string, password: string): Promise<boolean> {
      this.loading = true
      this.error = null
      this.pendingReset = null
      try {
        const payload = await client.post<LoginResponse>('/auth/login', { username, password })
        if ('must_reset_password' in payload) {
          this.pendingReset = payload.username
        } else {
          this.currentUser = payload
        }
        return true
      } catch (err) {
        this.error = (err as Error).message
        return false
      } finally {
        this.loading = false
      }
    },

    async resetPassword(username: string, currentPassword: string, newPassword: string): Promise<boolean> {
      this.loading = true
      this.error = null
      try {
        this.currentUser = await client.post<AuthUser>('/auth/reset-password', {
          username,
          current_password: currentPassword,
          new_password: newPassword,
        })
        this.pendingReset = null
        return true
      } catch (err) {
        this.error = (err as Error).message
        return false
      } finally {
        this.loading = false
      }
    },

    async logout(): Promise<boolean> {
      this.loading = true
      this.error = null
      try {
        await client.post('/auth/logout')
        this.currentUser = null
        return true
      } catch (err) {
        this.error = (err as Error).message
        return false
      } finally {
        this.loading = false
      }
    },

    async fetchMe(): Promise<boolean> {
      // Session probe on page load — must not trigger the interceptor's
      // hard 401 redirect, or every unauthenticated load would bounce to
      // /login before the router guard gets a chance to run.
      this.loading = true
      try {
        this.currentUser = await client.get<AuthUser>('/auth/me', { skip401Redirect: true })
        return true
      } catch {
        this.currentUser = null
        return false
      } finally {
        this.loading = false
      }
    },

    async claim(username: string, password: string): Promise<boolean> {
      this.loading = true
      this.error = null
      try {
        await client.post('/auth/claim', { username, password })
        return true
      } catch (err) {
        this.error = (err as Error).message
        return false
      } finally {
        this.loading = false
      }
    },

    async changePassword(currentPassword: string, newPassword: string): Promise<boolean> {
      this.loading = true
      this.error = null
      try {
        await client.post('/auth/change-password', {
          current_password: currentPassword,
          new_password: newPassword,
        })
        return true
      } catch (err) {
        this.error = (err as Error).message
        return false
      } finally {
        this.loading = false
      }
    },
  },
})
