import { defineStore } from 'pinia'
import client from '../api/client'
import type { Device, DeviceCreatePayload } from '../types'

interface DevicesState {
  items: Device[]
  loading: boolean
  error: string | null
}

export const useDevicesStore = defineStore('devices', {
  state: (): DevicesState => ({
    items: [],
    loading: false,
    error: null,
  }),
  actions: {
    async list(): Promise<boolean> {
      this.loading = true
      this.error = null
      try {
        this.items = await client.get<Device[]>('/devices/')
        return true
      } catch (err) {
        this.error = (err as Error).message
        return false
      } finally {
        this.loading = false
      }
    },

    async add(device: DeviceCreatePayload): Promise<boolean> {
      this.loading = true
      this.error = null
      try {
        await client.post('/devices/', device)
        await this.list()
        return true
      } catch (err) {
        this.error = (err as Error).message
        return false
      } finally {
        this.loading = false
      }
    },

    async remove(serial: string): Promise<boolean> {
      this.loading = true
      this.error = null
      try {
        await client.delete(`/devices/${serial}`)
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
