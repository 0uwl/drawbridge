<script setup lang="ts">
import { ref } from 'vue'
import { useSessionsStore } from '../stores/sessions'
import DeviceTabs from '../components/DeviceTabs.vue'
import { formatTimestamp } from '../utils/format'
import { stateBadgeClass, formatStateLabel } from '../utils/provisioningStates'
import { usePolling } from '../composables/usePolling'

const sessions = useSessionsStore()
usePolling(() => sessions.list(), 4000)

const pendingCancel = ref<string | null>(null)

async function confirmCancel(): Promise<void> {
  const serial = pendingCancel.value
  pendingCancel.value = null
  if (serial === null) return
  await sessions.cancel(serial)
}
</script>

<template>
  <div class="p-6">
    <div class="flex items-center justify-between mb-4">
      <h1 class="text-2xl font-bold">Devices</h1>
      <button class="btn btn-ghost btn-sm" :disabled="sessions.loading" @click="sessions.list()">Refresh</button>
    </div>

    <DeviceTabs />

    <div v-if="sessions.error" class="alert alert-error mb-4">{{ sessions.error }}</div>

    <div class="overflow-x-auto">
      <table class="table">
        <thead>
          <tr>
            <th>Serial</th>
            <th>MAC</th>
            <th>IP</th>
            <th>Image</th>
            <th>Config</th>
            <th>State</th>
            <th>Approved</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="s in sessions.items"
            :key="s.serial"
            class="cursor-pointer hover"
            @click="$router.push({ path: '/device-logs', query: { serial: s.serial } })"
          >
            <td class="font-mono">{{ s.serial }}</td>
            <td>{{ s.mac ?? '—' }}</td>
            <td>{{ s.ip ?? '—' }}</td>
            <td>{{ s.image ?? '—' }}</td>
            <td>{{ s.config_file ?? '—' }}</td>
            <td><span class="badge" :class="stateBadgeClass(s.state)">{{ formatStateLabel(s.state) }}</span></td>
            <td :title="s.approved_at">{{ formatTimestamp(s.approved_at) }}</td>
            <td>
              <button
                v-if="s.stale"
                class="btn btn-error btn-xs"
                :title="`Last seen ${formatTimestamp(s.last_seen_at)}`"
                @click.stop="pendingCancel = s.serial"
              >
                Cancel
              </button>
            </td>
          </tr>
          <tr v-if="sessions.items.length === 0">
            <td colspan="8" class="text-center text-base-content/60">No active provisioning sessions</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Cancel confirmation modal -->
    <dialog class="modal" :open="pendingCancel !== null">
      <div class="modal-box">
        <h3 class="font-bold text-lg">Cancel session?</h3>
        <p class="py-4">
          This cancels the stuck provisioning session for
          <span class="font-mono">{{ pendingCancel }}</span> and records it as cancelled in the provisioning log.
          This cannot be undone.
        </p>
        <div class="modal-action">
          <button class="btn" @click="pendingCancel = null">Keep waiting</button>
          <button class="btn btn-error" @click="confirmCancel">Cancel session</button>
        </div>
      </div>
    </dialog>
  </div>
</template>
