import { defineStore } from 'pinia'
import client from '../api/client'

export const useSessionsStore = defineStore('sessions', {
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
        this.items = await client.get('/devices/sessions')
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
