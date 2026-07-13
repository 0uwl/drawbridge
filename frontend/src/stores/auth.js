import { defineStore } from 'pinia'
import client from '../api/client'

export const useAuthStore = defineStore('auth', {
  state: () => ({
    currentUser: null,
    // Username pending a forced password reset (see docs/authentication.md,
    // "Bootstrap admin password sources") — set when login() succeeds but
    // the server withheld a session because must_reset_password is set.
    pendingReset: null,
    loading: false,
    error: null,
  }),
  actions: {
    async login(username, password) {
      this.loading = true
      this.error = null
      this.pendingReset = null
      try {
        const payload = await client.post('/auth/login', { username, password })
        if (payload?.must_reset_password) {
          this.pendingReset = payload.username
        } else {
          this.currentUser = payload
        }
        return true
      } catch (err) {
        this.error = err.message
        return false
      } finally {
        this.loading = false
      }
    },

    async resetPassword(username, currentPassword, newPassword) {
      this.loading = true
      this.error = null
      try {
        this.currentUser = await client.post('/auth/reset-password', {
          username,
          current_password: currentPassword,
          new_password: newPassword,
        })
        this.pendingReset = null
        return true
      } catch (err) {
        this.error = err.message
        return false
      } finally {
        this.loading = false
      }
    },

    async logout() {
      this.loading = true
      this.error = null
      try {
        await client.post('/auth/logout')
        this.currentUser = null
        return true
      } catch (err) {
        this.error = err.message
        return false
      } finally {
        this.loading = false
      }
    },

    async fetchMe() {
      // Session probe on page load — must not trigger the interceptor's
      // hard 401 redirect, or every unauthenticated load would bounce to
      // /login before the router guard gets a chance to run.
      this.loading = true
      try {
        this.currentUser = await client.get('/auth/me', { skip401Redirect: true })
        return true
      } catch {
        this.currentUser = null
        return false
      } finally {
        this.loading = false
      }
    },

    async claim(username, password) {
      this.loading = true
      this.error = null
      try {
        await client.post('/auth/claim', { username, password })
        return true
      } catch (err) {
        this.error = err.message
        return false
      } finally {
        this.loading = false
      }
    },

    async changePassword(currentPassword, newPassword) {
      this.loading = true
      this.error = null
      try {
        await client.post('/auth/change-password', {
          current_password: currentPassword,
          new_password: newPassword,
        })
        return true
      } catch (err) {
        this.error = err.message
        return false
      } finally {
        this.loading = false
      }
    },
  },
})
