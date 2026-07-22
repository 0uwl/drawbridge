import { defineStore } from 'pinia'
import client from '../api/client'
import type { DeviceLogEntry } from '../types'

interface DeviceLogsState {
  entries: DeviceLogEntry[]
  serial: string | undefined
  hasFetched: boolean
  loading: boolean
  error: string | null
}

export const useDeviceLogsStore = defineStore('deviceLogs', {
  state: (): DeviceLogsState => ({
    entries: [],
    serial: undefined,
    hasFetched: false,
    loading: false,
    error: null,
  }),
  actions: {
    // Resets to a fresh, unfetched state for the given serial — call before
    // the first poll() so it does a full fetch rather than treating an
    // empty entries array as "nothing new" for the previous serial.
    setSerial(serial?: string): void {
      this.serial = serial
      this.entries = []
      this.hasFetched = false
    },

    // entries are newest-first (see drawbridge/queries.py's
    // list_device_logs), so entries[0] holds the highest id seen so far.
    // First call does a full fetch; every call after that only asks for
    // rows newer than that, and prepends them — avoids re-fetching (and
    // re-rendering) the whole, potentially long-running list on every poll.
    async poll(): Promise<boolean> {
      this.loading = true
      this.error = null
      try {
        const afterId = this.hasFetched ? this.entries[0]?.id : undefined
        const newEntries = await client.get<DeviceLogEntry[]>('/device-logs', {
          params: { serial: this.serial, after_id: afterId },
        })
        this.entries = this.hasFetched ? [...newEntries, ...this.entries] : newEntries
        this.hasFetched = true
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
