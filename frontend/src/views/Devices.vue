<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useDevicesStore } from '../stores/devices'
import { useLogStore } from '../stores/log'
import { useFilesStore } from '../stores/files'
import { useSessionsStore } from '../stores/sessions'
import { useDeviceLogsStore } from '../stores/deviceLogs'
import DeviceTabs from '../components/DeviceTabs.vue'
import { formatTimestamp } from '../utils/format'
import { stateBadgeClass, formatStateLabel } from '../utils/provisioningStates'
import { usePolling } from '../composables/usePolling'
import type { Device, DeviceCreatePayload, DeviceUpdatePayload, ProvisioningLog } from '../types'

const devices = useDevicesStore()
const log = useLogStore()
const files = useFilesStore()
const sessions = useSessionsStore()
const deviceLogs = useDeviceLogsStore()
onMounted(() => {
  files.list()
  sessions.list()
})
// Same cadence as Sessions.vue's own polling — this page has no other way
// to learn a device finished provisioning: its ProvisioningSession (and
// therefore its row on the Sessions tab) is gone by then, and the
// "Provisioned" badge here is sourced from this same log fetch. sessions is
// polled too so the detail modal's "current version"/model/image fields
// (self-reported, only ever live on the session — see docs/decisions.md,
// "Facts-first provisioning") stay fresh while a modal is open; the device
// log feed only actually polls while a modal is open.
usePolling(() => {
  devices.list()
  log.fetchLog()
  sessions.list()
  if (detailSerial.value !== null) deviceLogs.poll()
}, 4000)

const images = computed(() => files.items.filter((f) => f.file_type === 'image'))
const configs = computed(() => files.items.filter((f) => f.file_type === 'config'))
// Versions come from the image catalog (parsed/set at upload — see
// docs/decisions.md, "Version-based image mapping"), not free text, so an
// allowlist entry can never name a version with no image behind it.
const imageVersions = computed(() => {
  const versions = images.value.map((f) => f.version).filter((v): v is string => v !== null)
  return Array.from(new Set(versions)).sort()
})

const showAddModal = ref(false)
const pendingRemove = ref<string | null>(null)
const editTarget = ref<string | null>(null)
const detailSerial = ref<string | null>(null)

function lastProvisioned(serial: string): ProvisioningLog | null {
  return log.entries
    .filter((e) => e.serial === serial && e.event === 'provision_complete')
    // ponytail: relies on ISO 8601 string sort order == chronological order
    .reduce<ProvisioningLog | null>((latest, e) => (latest === null || e.timestamp > latest.timestamp ? e : latest), null)
}

function openDetail(serial: string): void {
  detailSerial.value = serial
  deviceLogs.setSerial(serial)
  deviceLogs.poll()
}

function closeDetail(): void {
  detailSerial.value = null
}

const detailDevice = computed(() => devices.items.find((d) => d.serial === detailSerial.value) ?? null)
// Self-reported facts (model, current version, resolved image) only ever
// live on the active ProvisioningSession, never copied onto Device — see
// docs/decisions.md, "Facts-first provisioning". Nothing to show here once
// the session completes; that's by design, not a bug.
const detailSession = computed(() => sessions.items.find((s) => s.serial === detailSerial.value) ?? null)

const wildcardMode = ref(false)

const form = reactive<DeviceCreatePayload>({
  serial: '',
  mac: '',
  description: '',
  version: '',
  config_file: '',
})

function resetForm(): void {
  form.serial = ''
  form.mac = ''
  form.description = ''
  form.version = ''
  form.config_file = ''
  wildcardMode.value = false
}

async function submitAdd(): Promise<void> {
  const ok = await devices.add({ ...form, serial: wildcardMode.value ? '*' : form.serial })
  if (ok) {
    resetForm()
    showAddModal.value = false
  }
}

const editForm = reactive<DeviceUpdatePayload>({
  mac: '',
  description: '',
  version: '',
  config_file: '',
})

function openEdit(d: Device): void {
  editTarget.value = d.serial
  editForm.mac = d.mac ?? ''
  editForm.description = d.description ?? ''
  editForm.version = d.version ?? ''
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
            <th>Desired version</th>
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
            @click="openDetail(d.serial)"
          >
            <td class="font-mono">{{ d.serial === '*' ? '* (allow all)' : d.serial }}</td>
            <td class="font-mono">{{ d.version ?? '—' }}</td>
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
            <td colspan="6" class="text-center text-base-content/60">No devices registered</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Add device modal -->
    <dialog class="modal" :open="showAddModal">
      <div class="modal-box max-w-3xl">
        <h3 class="font-bold text-lg mb-4">Add device</h3>
        <form @submit.prevent="submitAdd">
          <label class="label cursor-pointer justify-start gap-2 mb-2">
            <input v-model="wildcardMode" type="checkbox" class="checkbox checkbox-sm" />
            <span class="label-text">
              Allow all devices (wildcard) — only for a local deployment with devices directly connected
            </span>
          </label>

          <div class="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-4">
            <label class="form-control">
              <span class="label-text">Serial *</span>
              <input
                v-model="form.serial"
                type="text"
                class="input input-bordered w-full"
                :disabled="wildcardMode"
                :required="!wildcardMode"
              />
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
              <span class="label-text">Desired version</span>
              <select v-model="form.version" class="select select-bordered max-w-64 truncate">
                <option value="">— none —</option>
                <option v-for="v in imageVersions" :key="v" :value="v">{{ v }}</option>
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
              <span class="label-text">Desired version</span>
              <select v-model="editForm.version" class="select select-bordered max-w-64 truncate">
                <option value="">— none —</option>
                <option v-for="v in imageVersions" :key="v" :value="v">{{ v }}</option>
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

    <!-- Detail modal: everything not shown on the row, plus the live log feed -->
    <dialog class="modal" :open="detailSerial !== null">
      <div class="modal-box max-w-3xl">
        <h3 class="font-bold text-lg mb-4 font-mono">{{ detailSerial }}</h3>

        <div class="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-2 text-sm mb-4">
          <div><span class="text-base-content/60">Description:</span> {{ detailDevice?.description ?? '—' }}</div>
          <div><span class="text-base-content/60">MAC:</span> {{ detailDevice?.mac ?? '—' }}</div>
          <div>
            <span class="text-base-content/60">Current version:</span>
            {{ detailSession?.version ?? '— (not currently provisioning)' }}
          </div>
          <div><span class="text-base-content/60">Device model:</span> {{ detailSession?.model ?? '—' }}</div>
          <div><span class="text-base-content/60">Image file:</span> {{ detailSession?.image ?? '—' }}</div>
          <div v-if="detailSession">
            <span class="text-base-content/60">Session state:</span>
            <span class="badge badge-sm" :class="stateBadgeClass(detailSession.state)">
              {{ formatStateLabel(detailSession.state) }}
            </span>
          </div>
        </div>

        <div class="text-sm font-semibold mb-1">Live log feed</div>
        <div v-if="deviceLogs.error" class="alert alert-error mb-2 text-sm">{{ deviceLogs.error }}</div>
        <div class="overflow-y-auto max-h-64 border border-base-300 rounded">
          <table class="table table-sm">
            <tbody>
              <tr v-for="e in deviceLogs.entries" :key="e.id">
                <td :title="e.timestamp" class="whitespace-nowrap">{{ formatTimestamp(e.timestamp) }}</td>
                <td>
                  <span class="badge badge-sm" :class="e.source === 'script' ? 'badge-info' : 'badge-neutral'">
                    {{ e.source }}
                  </span>
                </td>
                <td>{{ e.message }}</td>
              </tr>
              <tr v-if="deviceLogs.entries.length === 0">
                <td colspan="3" class="text-center text-base-content/60">No log entries</td>
              </tr>
            </tbody>
          </table>
        </div>

        <div class="modal-action">
          <button class="btn" @click="closeDetail">Close</button>
        </div>
      </div>
    </dialog>
  </div>
</template>
