<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useDevicesStore } from '../stores/devices'
import { useLogStore } from '../stores/log'
import { useFilesStore } from '../stores/files'
import DeviceTabs from '../components/DeviceTabs.vue'
import { formatTimestamp } from '../utils/format'

const devices = useDevicesStore()
const log = useLogStore()
const files = useFilesStore()
onMounted(() => {
  devices.list()
  log.fetchLog()
  files.list()
})

const images = computed(() => files.items.filter((f) => f.file_type === 'image'))
const configs = computed(() => files.items.filter((f) => f.file_type === 'config'))
const scripts = computed(() => files.items.filter((f) => f.file_type === 'script'))

const showAddModal = ref(false)
const pendingRemove = ref(null)

function lastProvisioned(serial) {
  return log.entries
    .filter((e) => e.serial === serial && e.event === 'provision_complete')
    .reduce((latest, e) => (latest === null || e.timestamp > latest.timestamp ? e : latest), null)
}

const form = reactive({
  serial: '',
  mac: '',
  description: '',
  image: '',
  config_file: '',
  script: '',
})

function resetForm() {
  form.serial = ''
  form.mac = ''
  form.description = ''
  form.image = ''
  form.config_file = ''
  form.script = ''
}

async function submitAdd() {
  const ok = await devices.add({ ...form })
  if (ok) {
    resetForm()
    showAddModal.value = false
  }
}

async function confirmRemove() {
  const serial = pendingRemove.value
  pendingRemove.value = null
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
          <tr v-for="d in devices.items" :key="d.serial">
            <td class="font-mono">{{ d.serial }}</td>
            <td>{{ d.mac ?? '—' }}</td>
            <td>{{ d.description ?? '—' }}</td>
            <td>{{ d.image ?? '—' }}</td>
            <td>{{ d.config_file ?? '—' }}</td>
            <td :title="d.added_at">{{ formatTimestamp(d.added_at) }}</td>
            <td>
              <span v-if="lastProvisioned(d.serial)" class="badge badge-success" :title="lastProvisioned(d.serial).timestamp">
                Provisioned {{ formatTimestamp(lastProvisioned(d.serial).timestamp) }}
              </span>
              <span v-else class="badge badge-ghost">Not yet provisioned</span>
            </td>
            <td>
              <button class="btn btn-error btn-xs" @click="pendingRemove = d.serial">Remove</button>
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
            <label class="form-control">
              <span class="label-text">Script</span>
              <select v-model="form.script" class="select select-bordered max-w-64 truncate">
                <option value="">— none —</option>
                <option v-for="f in scripts" :key="f.filename" :value="f.filename">{{ f.filename }}</option>
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
