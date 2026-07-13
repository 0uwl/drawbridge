import { defineStore } from 'pinia'
import client from '../api/client'

export const useLogStore = defineStore('log', {
  state: () => ({
    entries: [],
    loading: false,
    error: null,
  }),
  actions: {
    async fetchLog() {
      this.loading = true
      this.error = null
      try {
        this.entries = await client.get('/log')
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
