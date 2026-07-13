import { defineStore } from 'pinia'
import filesClient from '../api/filesClient'
import { TYPE_PLURAL } from '../utils/fileTypes'

const CONCURRENCY = 3

export const useFilesStore = defineStore('files', {
  state: () => ({
    items: [],
    queue: [],
    loading: false,
    error: null,
    _draining: false,
  }),
  actions: {
    async list() {
      this.loading = true
      this.error = null
      try {
        const lists = await Promise.all(
          Object.values(TYPE_PLURAL).map((plural) => filesClient.get(`/${plural}`)),
        )
        this.items = lists.flat().sort((a, b) => (a.uploaded_at < b.uploaded_at ? 1 : -1))
        return true
      } catch (err) {
        this.error = err.message
        return false
      } finally {
        this.loading = false
      }
    },

    async remove(fileType, filename) {
      this.loading = true
      this.error = null
      try {
        await filesClient.delete(`/${TYPE_PLURAL[fileType]}/${filename}`)
        await this.list()
        return true
      } catch (err) {
        this.error = err.message
        return false
      } finally {
        this.loading = false
      }
    },

    // `files` is [{ file: File, fileType }, ...] — already filtered to
    // known types by the caller.
    enqueueAndStart(files) {
      for (const { file, fileType } of files) {
        this.queue.push({
          id: crypto.randomUUID(),
          file,
          fileType,
          filename: file.name,
          status: 'queued',
          progress: 0,
          controller: null,
          error: null,
        })
      }
      this._drainQueue()
    },

    cancelUpload(id) {
      const item = this.queue.find((i) => i.id === id)
      if (!item) return
      if (item.status === 'uploading') {
        item.controller?.abort()
      } else if (item.status === 'queued') {
        item.status = 'canceled'
      }
    },

    clearFinished() {
      this.queue = this.queue.filter((i) => i.status === 'queued' || i.status === 'uploading')
    },

    async _drainQueue() {
      if (this._draining) return
      this._draining = true
      while (this.queue.some((i) => i.status === 'queued')) {
        const workers = Array.from({ length: CONCURRENCY }, () => this._runWorker())
        await Promise.all(workers)
      }
      this._draining = false
      await this.list()
    },

    async _runWorker() {
      // eslint-disable-next-line no-constant-condition
      while (true) {
        const item = this.queue.find((i) => i.status === 'queued')
        if (!item) return
        await this._uploadItem(item)
      }
    },

    async _uploadItem(item) {
      item.status = 'uploading'
      item.controller = new AbortController()
      const formData = new FormData()
      formData.append('file', item.file)
      try {
        await filesClient.post(`/${TYPE_PLURAL[item.fileType]}`, formData, {
          signal: item.controller.signal,
          onUploadProgress: (evt) => {
            if (evt.total) item.progress = Math.round((evt.loaded / evt.total) * 100)
          },
        })
        item.status = 'done'
        item.progress = 100
      } catch (err) {
        if (item.controller.signal.aborted) {
          item.status = 'canceled'
        } else {
          item.status = 'error'
          item.error = err.message
        }
      }
    },
  },
})
