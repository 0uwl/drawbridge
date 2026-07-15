import { defineStore } from 'pinia'
import client from '../api/client'
import type { AdminUser } from '../types'

interface UsersState {
  items: AdminUser[]
  loading: boolean
  error: string | null
}

export const useUsersStore = defineStore('users', {
  state: (): UsersState => ({
    items: [],
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
        await client.post('/users', { username, role })
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
