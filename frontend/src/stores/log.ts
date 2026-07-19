import { defineStore } from 'pinia'
import client from '../api/client'
import type { ProvisioningLog } from '../types'

interface LogState {
  entries: ProvisioningLog[]
  loading: boolean
  error: string | null
}

export const useLogStore = defineStore('log', {
  state: (): LogState => ({
    entries: [],
    loading: false,
    error: null,
  }),
  actions: {
    async fetchLog(): Promise<boolean> {
      this.loading = true
      this.error = null
      try {
        this.entries = await client.get<ProvisioningLog[]>('/log')
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
