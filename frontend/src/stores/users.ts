import { defineStore } from 'pinia'
import client from '../api/client'
import type { AdminUser, ClaimTokenResponse } from '../types'

interface UsersState {
  items: AdminUser[]
  // Claim token from the most recent create()/resetPassword() call — shown
  // once to the admin, then discarded; the backend never re-shows it either.
  lastClaimToken: string | null
  loading: boolean
  error: string | null
}

export const useUsersStore = defineStore('users', {
  state: (): UsersState => ({
    items: [],
    lastClaimToken: null,
    loading: false,
    error: null,
  }),
  actions: {
    async list(): Promise<boolean> {
      this.loading = true
      this.error = null
      try {
        this.items = await client.get<AdminUser[]>('/users')
        return true
      } catch (err) {
        this.error = (err as Error).message
        return false
      } finally {
        this.loading = false
      }
    },

    async create(username: string, role: AdminUser['role']): Promise<boolean> {
      this.loading = true
      this.error = null
      try {
        const created = await client.post<ClaimTokenResponse>('/users', { username, role })
        this.lastClaimToken = created.claim_token
        await this.list()
        return true
      } catch (err) {
        this.error = (err as Error).message
        return false
      } finally {
        this.loading = false
      }
    },

    async resetPassword(id: number): Promise<boolean> {
      this.loading = true
      this.error = null
      try {
        const reset = await client.post<ClaimTokenResponse>(`/users/${id}/reset-password`)
        this.lastClaimToken = reset.claim_token
        await this.list()
        return true
      } catch (err) {
        this.error = (err as Error).message
        return false
      } finally {
        this.loading = false
      }
    },

    async updateRole(id: number, role: AdminUser['role']): Promise<boolean> {
      this.loading = true
      this.error = null
      try {
        await client.put(`/users/${id}`, { role })
        await this.list()
        return true
      } catch (err) {
        this.error = (err as Error).message
        return false
      } finally {
        this.loading = false
      }
    },

    async remove(id: number): Promise<boolean> {
      this.loading = true
      this.error = null
      try {
        await client.delete(`/users/${id}`)
        await this.list()
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
