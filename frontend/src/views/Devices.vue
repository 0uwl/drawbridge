<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useDevicesStore } from '../stores/devices'
import { useLogStore } from '../stores/log'
import { useFilesStore } from '../stores/files'
import DeviceTabs from '../components/DeviceTabs.vue'
import { formatTimestamp } from '../utils/format'
import { usePolling } from '../composables/usePolling'
import type { Device, DeviceCreatePayload, DeviceUpdatePayload, ProvisioningLog } from '../types'

const devices = useDevicesStore()
const log = useLogStore()
const files = useFilesStore()
onMounted(() => {
  files.list()
})
// Same cadence as Sessions.vue's own polling — this page has no other way
// to learn a device finished provisioning: its ProvisioningSession (and
// therefore its row on the Sessions tab) is gone by then, and the
// "Provisioned" badge here is sourced from this same log fetch.
usePolling(() => {
  devices.list()
  log.fetchLog()
}, 4000)

const images = computed(() => files.items.filter((f) => f.file_type === 'image'))
const configs = computed(() => files.items.filter((f) => f.file_type === 'config'))

const showAddModal = ref(false)
const pendingRemove = ref<string | null>(null)
const editTarget = ref<string | null>(null)

function lastProvisioned(serial: string): ProvisioningLog | null {
  return log.entries
    .filter((e) => e.serial === serial && e.event === 'provision_complete')
    // ponytail: relies on ISO 8601 string sort order == chronological order
    .reduce<ProvisioningLog | null>((latest, e) => (latest === null || e.timestamp > latest.timestamp ? e : latest), null)
}

const form = reactive<DeviceCreatePayload>({
  serial: '',
  mac: '',
  description: '',
  image: '',
  config_file: '',
})

function resetForm(): void {
  form.serial = ''
  form.mac = ''
  form.description = ''
  form.image = ''
  form.config_file = ''
}

async function submitAdd(): Promise<void> {
  const ok = await devices.add({ ...form })
  if (ok) {
    resetForm()
    showAddModal.value = false
  }
}

const editForm = reactive<DeviceUpdatePayload>({
  mac: '',
  description: '',
  image: '',
  config_file: '',
})

function openEdit(d: Device): void {
  editTarget.value = d.serial
  editForm.mac = d.mac ?? ''
  editForm.description = d.description ?? ''
  editForm.image = d.image ?? ''
  editForm.config_file = d.config_file ?? ''
}

async function submitEdit(): Promise<void> {
  const serial = editTarget.value
  if (serial === null) return
  const ok = await devices.update(serial, { ...editForm })
  if (ok) {
    editTarget.value = null
  }
}

async function confirmRemove(): Promise<void> {
  const serial = pendingRemove.value
  pendingRemove.value = null
  if (serial === null) return
  await devices.remove(serial)
}
</script>

<template>
  <div class="p-6">
    <div class="flex items-center justify-between mb-4">
      <h1 class="text-2xl font-bold">Devices</h1>
      <button class="btn btn-primary btn-sm" @click="showAddModal = true">Add device</button>
    </div>

    <DeviceTabs />

    <div v-if="devices.error" class="alert alert-error mb-4">{{ devices.error }}</div>
    <div v-if="log.error" class="alert alert-error mb-4">{{ log.error }}</div>

    <div class="overflow-x-auto">
      <table class="table">
        <thead>
          <tr>
            <th>Serial</th>
            <th>MAC</th>
            <th>Description</th>
            <th>Image</th>
            <th>Config</th>
            <th>Added</th>
            <th>Provisioning</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="d in devices.items"
            :key="d.serial"
            class="cursor-pointer hover"
            @click="$router.push({ path: '/device-logs', query: { serial: d.serial } })"
          >
            <td class="font-mono">{{ d.serial }}</td>
            <td>{{ d.mac ?? '—' }}</td>
            <td>{{ d.description ?? '—' }}</td>
            <td>{{ d.image ?? '—' }}</td>
            <td>{{ d.config_file ?? '—' }}</td>
            <td :title="d.added_at">{{ formatTimestamp(d.added_at) }}</td>
            <td>
              <span v-if="lastProvisioned(d.serial)" class="badge badge-success" :title="lastProvisioned(d.serial)?.timestamp">
                Provisioned {{ formatTimestamp(lastProvisioned(d.serial)?.timestamp) }}
              </span>
              <span v-else class="badge badge-ghost">Not yet provisioned</span>
            </td>
            <td>
              <div class="flex gap-2">
                <button class="btn btn-xs" @click.stop="openEdit(d)">Edit</button>
                <button class="btn btn-error btn-xs" @click.stop="pendingRemove = d.serial">Remove</button>
              </div>
            </td>
          </tr>
          <tr v-if="devices.items.length === 0">
            <td colspan="8" class="text-center text-base-content/60">No devices registered</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Add device modal -->
    <dialog class="modal" :open="showAddModal">
      <div class="modal-box max-w-3xl">
        <h3 class="font-bold text-lg mb-4">Add device</h3>
        <form @submit.prevent="submitAdd">
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-4">
            <label class="form-control">
              <span class="label-text">Serial *</span>
              <input v-model="form.serial" type="text" class="input input-bordered w-full" required />
            </label>
            <label class="form-control">
              <span class="label-text">MAC</span>
              <input v-model="form.mac" type="text" class="input input-bordered w-full" />
            </label>
            <label class="form-control sm:col-span-2">
              <span class="label-text">Description</span>
              <input v-model="form.description" type="text" class="input input-bordered w-full" />
            </label>
          </div>

          <div class="flex flex-wrap gap-4 mt-4">
            <label class="form-control">
              <span class="label-text">Image</span>
              <select v-model="form.image" class="select select-bordered max-w-64 truncate">
                <option value="">— none —</option>
                <option v-for="f in images" :key="f.filename" :value="f.filename">{{ f.filename }}</option>
              </select>
            </label>
            <label class="form-control">
              <span class="label-text">Config file</span>
              <select v-model="form.config_file" class="select select-bordered max-w-64 truncate">
                <option value="">— none —</option>
                <option v-for="f in configs" :key="f.filename" :value="f.filename">{{ f.filename }}</option>
              </select>
            </label>
          </div>

          <div class="modal-action">
            <button type="button" class="btn" @click="showAddModal = false">Cancel</button>
            <button type="submit" class="btn btn-primary" :disabled="devices.loading">Add</button>
          </div>
        </form>
      </div>
    </dialog>

    <!-- Edit device modal -->
    <dialog class="modal" :open="editTarget !== null">
      <div class="modal-box max-w-3xl">
        <h3 class="font-bold text-lg mb-4">Edit device</h3>
        <form @submit.prevent="submitEdit">
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-4">
            <label class="form-control">
              <span class="label-text">Serial</span>
              <input :value="editTarget" type="text" class="input input-bordered w-full font-mono" disabled />
            </label>
            <label class="form-control">
              <span class="label-text">MAC</span>
              <input v-model="editForm.mac" type="text" class="input input-bordered w-full" />
            </label>
            <label class="form-control sm:col-span-2">
              <span class="label-text">Description</span>
              <input v-model="editForm.description" type="text" class="input input-bordered w-full" />
            </label>
          </div>

          <div class="flex flex-wrap gap-4 mt-4">
            <label class="form-control">
              <span class="label-text">Image</span>
              <select v-model="editForm.image" class="select select-bordered max-w-64 truncate">
                <option value="">— none —</option>
                <option v-for="f in images" :key="f.filename" :value="f.filename">{{ f.filename }}</option>
              </select>
            </label>
            <label class="form-control">
              <span class="label-text">Config file</span>
              <select v-model="editForm.config_file" class="select select-bordered max-w-64 truncate">
                <option value="">— none —</option>
                <option v-for="f in configs" :key="f.filename" :value="f.filename">{{ f.filename }}</option>
              </select>
            </label>
          </div>

          <div class="modal-action">
            <button type="button" class="btn" @click="editTarget = null">Cancel</button>
            <button type="submit" class="btn btn-primary" :disabled="devices.loading">Save</button>
          </div>
        </form>
      </div>
    </dialog>

    <!-- Remove confirmation modal -->
    <dialog class="modal" :open="pendingRemove !== null">
      <div class="modal-box">
        <h3 class="font-bold text-lg">Remove device?</h3>
        <p class="py-4">
          This removes <span class="font-mono">{{ pendingRemove }}</span> from the allowlist. This cannot be undone.
        </p>
        <div class="modal-action">
          <button class="btn" @click="pendingRemove = null">Cancel</button>
          <button class="btn btn-error" @click="confirmRemove">Remove</button>
        </div>
      </div>
    </dialog>
  </div>
</template>
