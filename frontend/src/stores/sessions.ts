import { defineStore } from 'pinia'
import client from '../api/client'
import type { ProvisioningSession } from '../types'

interface SessionsState {
  items: ProvisioningSession[]
  loading: boolean
  error: string | null
}

export const useSessionsStore = defineStore('sessions', {
  state: (): SessionsState => ({
    items: [],
    loading: false,
    error: null,
  }),
  actions: {
    async list(): Promise<boolean> {
      this.loading = true
      this.error = null
      try {
        this.items = await client.get<ProvisioningSession[]>('/devices/sessions')
        return true
      } catch (err) {
        this.error = (err as Error).message
        return false
      } finally {
        this.loading = false
      }
    },

    async cancel(serial: string): Promise<boolean> {
      this.loading = true
      this.error = null
      try {
        await client.delete(`/devices/sessions/${serial}`)
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
