<script setup lang="ts">
import { useRoute } from 'vue-router'
import { useDeviceLogsStore } from '../stores/deviceLogs'
import { formatTimestamp } from '../utils/format'
import { usePolling } from '../composables/usePolling'

const route = useRoute()
const deviceLogs = useDeviceLogsStore()
const serial = typeof route.query.serial === 'string' ? route.query.serial : undefined
deviceLogs.setSerial(serial)
usePolling(() => deviceLogs.poll(), 2000)
</script>

<template>
  <div class="p-6">
    <h1 class="text-2xl font-bold mb-4">
      Device Logs<span v-if="serial" class="font-mono"> — {{ serial }}</span>
    </h1>

    <div v-if="deviceLogs.error" class="alert alert-error mb-4">{{ deviceLogs.error }}</div>

    <div class="overflow-x-auto">
      <table class="table">
        <thead>
          <tr>
            <th>Timestamp</th>
            <th>Serial</th>
            <th>Source</th>
            <th>Message</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="e in deviceLogs.entries" :key="e.id">
            <td :title="e.timestamp">{{ formatTimestamp(e.timestamp) }}</td>
            <td class="font-mono">{{ e.serial ?? '—' }}</td>
            <td>
              <span class="badge" :class="e.source === 'script' ? 'badge-info' : 'badge-neutral'">
                {{ e.source }}
              </span>
            </td>
            <td>{{ e.message }}</td>
          </tr>
          <tr v-if="deviceLogs.entries.length === 0">
            <td colspan="4" class="text-center text-base-content/60">No log entries</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
