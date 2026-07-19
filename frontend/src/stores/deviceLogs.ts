import { defineStore } from 'pinia'
import client from '../api/client'
import type { DeviceLogEntry } from '../types'

interface DeviceLogsState {
  entries: DeviceLogEntry[]
  loading: boolean
  error: string | null
}

export const useDeviceLogsStore = defineStore('deviceLogs', {
  state: (): DeviceLogsState => ({
    entries: [],
    loading: false,
    error: null,
  }),
  actions: {
    async fetchDeviceLogs(serial?: string): Promise<boolean> {
      this.loading = true
      this.error = null
      try {
        this.entries = await client.get<DeviceLogEntry[]>('/device-logs', { params: { serial } })
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
