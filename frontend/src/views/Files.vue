<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useFilesStore } from '../stores/files'
import { formatTimestamp, formatBytes } from '../utils/format'
import { inferFileType, acceptAttr, TYPE_LABELS } from '../utils/fileTypes'
import type { FileType, StagedFile, UploadStatus } from '../types'

const files = useFilesStore()
onMounted(() => {
  files.list()
})

const showUploadModal = ref(false)
const pendingRemove = ref<{ fileType: FileType; filename: string } | null>(null)
const staged = ref<StagedFile[]>([]) // pre-submit selection, not yet queued
const skippedCount = ref(0)

function openUploadModal(): void {
  files.clearFinished()
  staged.value = []
  skippedCount.value = 0
  showUploadModal.value = true
}

function onSelect(event: Event): void {
  const target = event.target as HTMLInputElement
  const picked = Array.from(target.files ?? [])
  const valid: StagedFile[] = []
  let skipped = 0
  for (const file of picked) {
    const fileType = inferFileType(file.name)
    if (fileType) {
      valid.push({ file, fileType })
    } else {
      skipped++
    }
  }
  staged.value = valid
  skippedCount.value = skipped
  target.value = ''
}

function removeStaged(index: number): void {
  staged.value.splice(index, 1)
}

function startUpload(): void {
  files.enqueueAndStart(staged.value)
  staged.value = []
  skippedCount.value = 0
}

async function confirmRemove(): Promise<void> {
  if (!pendingRemove.value) return
  const { fileType, filename } = pendingRemove.value
  pendingRemove.value = null
  await files.remove(fileType, filename)
}

function statusBadgeClass(status: UploadStatus): string {
  return {
    queued: 'badge-ghost',
    uploading: 'badge-info',
    done: 'badge-success',
    error: 'badge-error',
    canceled: 'badge-ghost',
  }[status]
}

function statusProgressClass(status: UploadStatus): string {
  return {
    queued: '',
    uploading: 'progress-info',
    done: 'progress-success',
    error: 'progress-error',
    canceled: '',
  }[status]
}
</script>

<template>
  <div class="p-6">
    <div class="flex items-center justify-between mb-4">
      <h1 class="text-2xl font-bold">Files</h1>
      <button class="btn btn-primary btn-sm" @click="openUploadModal">Upload files</button>
    </div>

    <div v-if="files.error" class="alert alert-error mb-4">{{ files.error }}</div>

    <div class="overflow-x-auto">
      <table class="table">
        <thead>
          <tr>
            <th>Filename</th>
            <th>Type</th>
            <th>Size</th>
            <th>Uploaded</th>
            <th>Uploaded by</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="f in files.items" :key="`${f.file_type}:${f.filename}`">
            <td class="font-mono">{{ f.filename }}</td>
            <td><span class="badge badge-outline">{{ TYPE_LABELS[f.file_type] }}</span></td>
            <td>{{ formatBytes(f.size_bytes) }}</td>
            <td :title="f.uploaded_at">{{ formatTimestamp(f.uploaded_at) }}</td>
            <td>{{ f.uploaded_by ?? '—' }}</td>
            <td>
              <button
                class="btn btn-error btn-xs"
                @click="pendingRemove = { fileType: f.file_type, filename: f.filename }"
              >
                Delete
              </button>
            </td>
          </tr>
          <tr v-if="files.items.length === 0">
            <td colspan="6" class="text-center text-base-content/60">No files uploaded</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Upload modal -->
    <dialog class="modal" :open="showUploadModal">
      <div class="modal-box max-w-2xl">
        <h3 class="font-bold text-lg mb-4">Upload files</h3>

        <input
          type="file"
          multiple
          :accept="acceptAttr()"
          class="file-input file-input-bordered w-full mb-3"
          @change="onSelect"
        />
        <p v-if="skippedCount > 0" class="text-warning text-sm mb-3">
          {{ skippedCount }} file{{ skippedCount === 1 ? '' : 's' }} skipped — unsupported type.
        </p>

        <div v-if="staged.length > 0" class="mb-4">
          <div class="text-sm font-semibold mb-1">Ready to upload</div>
          <ul class="flex flex-col gap-1 max-h-48 overflow-y-auto">
            <li
              v-for="(s, i) in staged"
              :key="s.file.name + i"
              class="flex items-center justify-between text-sm bg-base-200 rounded px-2 py-1"
            >
              <span class="font-mono truncate">{{ s.file.name }}</span>
              <span class="flex items-center gap-2 shrink-0">
                <span class="badge badge-outline badge-sm">{{ TYPE_LABELS[s.fileType] }}</span>
                <span class="text-base-content/60">{{ formatBytes(s.file.size) }}</span>
                <button class="btn btn-ghost btn-xs" @click="removeStaged(i)">Remove</button>
              </span>
            </li>
          </ul>
          <button class="btn btn-primary btn-sm mt-3" @click="startUpload">
            Start upload ({{ staged.length }})
          </button>
        </div>

        <div v-if="files.queue.length > 0">
          <div class="text-sm font-semibold mb-1">Queue</div>
          <ul class="flex flex-col gap-2 max-h-64 overflow-y-auto">
            <li v-for="item in files.queue" :key="item.id" class="text-sm bg-base-200 rounded px-2 py-2">
              <div class="flex items-center justify-between mb-1">
                <span class="font-mono truncate">{{ item.filename }}</span>
                <span class="flex items-center gap-2 shrink-0">
                  <span class="badge badge-outline badge-sm">{{ TYPE_LABELS[item.fileType] }}</span>
                  <span class="badge badge-sm" :class="statusBadgeClass(item.status)">
                    {{ item.status === 'error' ? item.error : item.status }}
                  </span>
                  <button
                    class="btn btn-ghost btn-xs"
                    :disabled="item.status === 'done' || item.status === 'error' || item.status === 'canceled'"
                    @click="files.cancelUpload(item.id)"
                  >
                    Cancel
                  </button>
                </span>
              </div>
              <progress class="progress w-full" :class="statusProgressClass(item.status)" :value="item.progress" max="100"></progress>
            </li>
          </ul>
        </div>

        <div class="modal-action">
          <button type="button" class="btn" @click="showUploadModal = false">Close</button>
        </div>
      </div>
    </dialog>

    <!-- Remove confirmation modal -->
    <dialog class="modal" :open="pendingRemove !== null">
      <div class="modal-box">
        <h3 class="font-bold text-lg">Delete file?</h3>
        <p class="py-4">
          This permanently deletes <span class="font-mono">{{ pendingRemove?.filename }}</span> from disk.
          This cannot be undone.
        </p>
        <div class="modal-action">
          <button class="btn" @click="pendingRemove = null">Cancel</button>
          <button class="btn btn-error" @click="confirmRemove">Delete</button>
        </div>
      </div>
    </dialog>
  </div>
</template>
