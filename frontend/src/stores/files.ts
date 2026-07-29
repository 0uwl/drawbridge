import { defineStore } from 'pinia'
import filesClient from '../api/filesClient'
import { TYPE_PLURAL } from '../utils/fileTypes'
import { ApiError } from '../types'
import type { FileType, QueueItem, StagedFile, ZTPFile } from '../types'

const CONCURRENCY = 3

interface FilesState {
  items: ZTPFile[]
  queue: QueueItem[]
  loading: boolean
  error: string | null
  // Set alongside error from the last failed action's ApiError.code — lets
  // callers (e.g. Files.vue's delete confirmation) branch on e.g.
  // 'image_in_use' without parsing the message string.
  errorCode: string | null
  _draining: boolean
}

export const useFilesStore = defineStore('files', {
  state: (): FilesState => ({
    items: [],
    queue: [],
    loading: false,
    error: null,
    errorCode: null,
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

    // confirm=true bypasses the 409 image_in_use warning (see
    // drawbridge/api/files.py) — an operator has already been shown the
    // affected allowlist entries and chosen to delete anyway.
    async remove(fileType: FileType, filename: string, confirm = false): Promise<boolean> {
      this.loading = true
      this.error = null
      this.errorCode = null
      try {
        const query = confirm ? '?confirm=true' : ''
        await filesClient.delete(`/${TYPE_PLURAL[fileType]}/${filename}${query}`)
        await this.list()
        return true
      } catch (err) {
        this.error = (err as Error).message
        this.errorCode = err instanceof ApiError ? err.code : null
        return false
      } finally {
        this.loading = false
      }
    },

    // `files` is already filtered to known types by the caller.
    enqueueAndStart(files: StagedFile[]): void {
      for (const { file, fileType, sha256, version } of files) {
        this.queue.push({
          id: crypto.randomUUID(),
          file,
          fileType,
          filename: file.name,
          sha256,
          version,
          status: 'queued',
          progress: 0,
          controller: null,
          error: null,
        })
      }
      this._drainQueue()
    },

    // Re-queues an item that failed with a version_conflict, this time
    // telling the backend to supersede the existing mapping — see
    // drawbridge/api/files.py's `replace` form field.
    retryUploadWithReplace(id: string): void {
      const item = this.queue.find((i) => i.id === id)
      if (!item) return
      item.replace = true
      item.conflict = false
      item.error = null
      item.status = 'queued'
      this._drainQueue()
    },

    async updateHash(fileType: FileType, filename: string, sha256: string): Promise<boolean> {
      this.loading = true
      this.error = null
      try {
        await filesClient.put(`/${TYPE_PLURAL[fileType]}/${filename}`, { sha256 })
        await this.list()
        return true
      } catch (err) {
        this.error = (err as Error).message
        return false
      } finally {
        this.loading = false
      }
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
      if (item.sha256) formData.append('sha256', item.sha256)
      if (item.fileType === 'image') {
        if (item.version) formData.append('version', item.version)
        if (item.replace) formData.append('replace', 'true')
      }
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
          item.conflict = err instanceof ApiError && err.code === 'version_conflict'
        }
      }
    },
  },
})
