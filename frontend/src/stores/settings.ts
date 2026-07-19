import { defineStore } from 'pinia'
import client from '../api/client'

interface SettingsState {
  // Backend always round-trips a string ('30'-style, or the literal
  // 'indefinite') — never a number, frontend never coerces it either.
  logRetentionDays: string | null
  loading: boolean
  error: string | null
}

export const useSettingsStore = defineStore('settings', {
  state: (): SettingsState => ({
    logRetentionDays: null,
    loading: false,
    error: null,
  }),
  actions: {
    async fetchLogRetention(): Promise<boolean> {
      this.loading = true
      this.error = null
      try {
        const payload = await client.get<{ log_retention_days: string | null }>('/settings/log-retention')
        this.logRetentionDays = payload.log_retention_days
        return true
      } catch (err) {
        this.error = (err as Error).message
        return false
      } finally {
        this.loading = false
      }
    },

    async updateLogRetention(value: string): Promise<boolean> {
      this.loading = true
      this.error = null
      try {
        const payload = await client.put<{ log_retention_days: string }>('/settings/log-retention', {
          log_retention_days: value,
        })
        this.logRetentionDays = payload.log_retention_days
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
