import { defineStore } from 'pinia'
import filesClient from '../api/filesClient'
import { TYPE_PLURAL } from '../utils/fileTypes'
import type { FileType, QueueItem, StagedFile, ZTPFile } from '../types'

const CONCURRENCY = 3

interface FilesState {
  items: ZTPFile[]
  queue: QueueItem[]
  loading: boolean
  error: string | null
  _draining: boolean
}

export const useFilesStore = defineStore('files', {
  state: (): FilesState => ({
    items: [],
    queue: [],
    loading: false,
    error: null,
    _draining: false,
  }),
  actions: {
    async list(): Promise<boolean> {
      this.loading = true
      this.error = null
      try {
        const lists = await Promise.all(
          Object.values(TYPE_PLURAL).map((plural) => filesClient.get<ZTPFile[]>(`/${plural}`)),
        )
        this.items = lists.flat().sort((a, b) => (a.uploaded_at < b.uploaded_at ? 1 : -1))
        return true
      } catch (err) {
        this.error = (err as Error).message
        return false
      } finally {
        this.loading = false
      }
    },

    async remove(fileType: FileType, filename: string): Promise<boolean> {
      this.loading = true
      this.error = null
      try {
        await filesClient.delete(`/${TYPE_PLURAL[fileType]}/${filename}`)
        await this.list()
        return true
      } catch (err) {
        this.error = (err as Error).message
        return false
      } finally {
        this.loading = false
      }
    },

    // `files` is already filtered to known types by the caller.
    enqueueAndStart(files: StagedFile[]): void {
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

    cancelUpload(id: string): void {
      const item = this.queue.find((i) => i.id === id)
      if (!item) return
      if (item.status === 'uploading') {
        item.controller?.abort()
      } else if (item.status === 'queued') {
        item.status = 'canceled'
      }
    },

    clearFinished(): void {
      this.queue = this.queue.filter((i) => i.status === 'queued' || i.status === 'uploading')
    },

    async _drainQueue(): Promise<void> {
      if (this._draining) return
      this._draining = true
      while (this.queue.some((i) => i.status === 'queued')) {
        const workers = Array.from({ length: CONCURRENCY }, () => this._runWorker())
        await Promise.all(workers)
      }
      this._draining = false
      await this.list()
    },

    async _runWorker(): Promise<void> {
      // eslint-disable-next-line no-constant-condition
      while (true) {
        const item = this.queue.find((i) => i.status === 'queued')
        if (!item) return
        await this._uploadItem(item)
      }
    },

    async _uploadItem(item: QueueItem): Promise<void> {
      item.status = 'uploading'
      item.controller = new AbortController()
      const controller = item.controller
      const formData = new FormData()
      formData.append('file', item.file)
      try {
        await filesClient.post(`/${TYPE_PLURAL[item.fileType]}`, formData, {
          signal: controller.signal,
          onUploadProgress: (evt) => {
            if (evt.total) item.progress = Math.round((evt.loaded / evt.total) * 100)
          },
        })
        item.status = 'done'
        item.progress = 100
      } catch (err) {
        if (controller.signal.aborted) {
          item.status = 'canceled'
        } else {
          item.status = 'error'
          item.error = (err as Error).message
        }
      }
    },
  },
})
