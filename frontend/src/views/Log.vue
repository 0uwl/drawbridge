<script setup>
import { onMounted } from 'vue'
import { useLogStore } from '../stores/log'

const log = useLogStore()
onMounted(() => log.fetchLog())
</script>

<template>
  <div class="p-6">
    <h1 class="text-2xl font-bold mb-4">Provisioning Log</h1>

    <div v-if="log.error" class="alert alert-error mb-4">{{ log.error }}</div>

    <div class="overflow-x-auto">
      <table class="table">
        <thead>
          <tr>
            <th>Timestamp</th>
            <th>Serial</th>
            <th>Event</th>
            <th>Image</th>
            <th>Config</th>
            <th>IP</th>
            <th>Detail</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="e in log.entries" :key="e.id">
            <td>{{ e.timestamp }}</td>
            <td class="font-mono">{{ e.serial }}</td>
            <td>
              <span class="badge" :class="e.event === 'provision_complete' ? 'badge-success' : 'badge-error'">
                {{ e.event }}
              </span>
            </td>
            <td>{{ e.image ?? '—' }}</td>
            <td>{{ e.config_file ?? '—' }}</td>
            <td>{{ e.ip ?? '—' }}</td>
            <td>{{ e.detail ?? '—' }}</td>
          </tr>
          <tr v-if="log.entries.length === 0">
            <td colspan="7" class="text-center text-base-content/60">No log entries</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
