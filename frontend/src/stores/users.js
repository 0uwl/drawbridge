import { defineStore } from 'pinia'
import client from '../api/client'

export const useUsersStore = defineStore('users', {
  state: () => ({
    items: [],
    loading: false,
    error: null,
  }),
  actions: {
    async list() {
      this.loading = true
      this.error = null
      try {
        this.items = await client.get('/users')
        return true
      } catch (err) {
        this.error = err.message
        return false
      } finally {
        this.loading = false
      }
    },

    async create(username, role) {
      this.loading = true
      this.error = null
      try {
        await client.post('/users', { username, role })
        await this.list()
        return true
      } catch (err) {
        this.error = err.message
        return false
      } finally {
        this.loading = false
      }
    },

    async updateRole(id, role) {
      this.loading = true
      this.error = null
      try {
        await client.put(`/users/${id}`, { role })
        await this.list()
        return true
      } catch (err) {
        this.error = err.message
        return false
      } finally {
        this.loading = false
      }
    },

    async remove(id) {
      this.loading = true
      this.error = null
      try {
        await client.delete(`/users/${id}`)
        await this.list()
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
