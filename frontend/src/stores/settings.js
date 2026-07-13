import { defineStore } from 'pinia'
import client from '../api/client'

export const useSettingsStore = defineStore('settings', {
  state: () => ({
    logRetentionDays: null,
    loading: false,
    error: null,
  }),
  actions: {
    async fetchLogRetention() {
      this.loading = true
      this.error = null
      try {
        const payload = await client.get('/settings/log-retention')
        this.logRetentionDays = payload.log_retention_days
        return true
      } catch (err) {
        this.error = err.message
        return false
      } finally {
        this.loading = false
      }
    },

    async updateLogRetention(value) {
      this.loading = true
      this.error = null
      try {
        const payload = await client.put('/settings/log-retention', { log_retention_days: value })
        this.logRetentionDays = payload.log_retention_days
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
