import { defineStore } from 'pinia'
import client from '../api/client'

export const useDevicesStore = defineStore('devices', {
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
        this.items = await client.get('/devices/')
        return true
      } catch (err) {
        this.error = err.message
        return false
      } finally {
        this.loading = false
      }
    },

    async add(device) {
      this.loading = true
      this.error = null
      try {
        await client.post('/devices/', device)
        await this.list()
        return true
      } catch (err) {
        this.error = err.message
        return false
      } finally {
        this.loading = false
      }
    },

    async remove(serial) {
      this.loading = true
      this.error = null
      try {
        await client.delete(`/devices/${serial}`)
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
