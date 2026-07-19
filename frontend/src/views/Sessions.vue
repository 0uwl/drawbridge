<script setup lang="ts">
import { onMounted } from 'vue'
import { useSessionsStore } from '../stores/sessions'
import DeviceTabs from '../components/DeviceTabs.vue'
import { formatTimestamp } from '../utils/format'

const sessions = useSessionsStore()
onMounted(() => sessions.list())
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
            <td><span class="badge badge-info">{{ s.state }}</span></td>
            <td :title="s.approved_at">{{ formatTimestamp(s.approved_at) }}</td>
          </tr>
          <tr v-if="sessions.items.length === 0">
            <td colspan="7" class="text-center text-base-content/60">No active provisioning sessions</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
