<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useAuthStore } from '../stores/auth'
import { useSettingsStore } from '../stores/settings'
import { useUsersStore } from '../stores/users'

const auth = useAuthStore()
const settings = useSettingsStore()
const users = useUsersStore()

const isAdmin = computed(() => auth.currentUser?.role === 'admin')

onMounted(() => {
  settings.fetchLogRetention()
  if (isAdmin.value) users.list()
})

const retentionInput = ref('')
async function saveRetention() {
  await settings.updateLogRetention(retentionInput.value)
}

const newUser = reactive({ username: '', role: 'operator' })
async function createUser() {
  const ok = await users.create(newUser.username, newUser.role)
  if (ok) {
    newUser.username = ''
    newUser.role = 'operator'
  }
}
</script>

<template>
  <div class="p-6 flex flex-col gap-8">
    <section>
      <h1 class="text-2xl font-bold mb-4">Log Retention</h1>
      <div v-if="settings.error" class="alert alert-error mb-4">{{ settings.error }}</div>
      <form @submit.prevent="saveRetention" class="flex items-end gap-3">
        <label class="form-control">
          <span class="label-text">Retention (days, or "indefinite")</span>
          <input
            v-model="retentionInput"
            type="text"
            class="input input-bordered"
            :placeholder="settings.logRetentionDays ?? ''"
            :disabled="!isAdmin"
          />
        </label>
        <button type="submit" class="btn btn-primary" :disabled="!isAdmin || settings.loading">Save</button>
      </form>
      <p class="text-sm text-base-content/60 mt-2">Current value: {{ settings.logRetentionDays ?? 'unset' }}</p>
    </section>

    <section v-if="isAdmin">
      <h2 class="text-2xl font-bold mb-4">Users</h2>
      <div v-if="users.error" class="alert alert-error mb-4">{{ users.error }}</div>

      <div class="overflow-x-auto mb-4">
        <table class="table">
          <thead>
            <tr>
              <th>Username</th>
              <th>Role</th>
              <th>Auth source</th>
              <th>Claimed</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="u in users.items" :key="u.id">
              <td>{{ u.username }}</td>
              <td>
                <span class="badge" :class="u.role === 'admin' ? 'badge-primary' : 'badge-ghost'">{{ u.role }}</span>
              </td>
              <td>{{ u.auth_source }}</td>
              <td>{{ u.is_claimed ? 'yes' : 'no' }}</td>
              <td class="flex gap-2">
                <button
                  class="btn btn-xs"
                  @click="users.updateRole(u.id, u.role === 'admin' ? 'operator' : 'admin')"
                >
                  Make {{ u.role === 'admin' ? 'operator' : 'admin' }}
                </button>
                <button class="btn btn-error btn-xs" @click="users.remove(u.id)">Delete</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <form @submit.prevent="createUser" class="flex items-end gap-3">
        <label class="form-control">
          <span class="label-text">New username</span>
          <input v-model="newUser.username" type="text" class="input input-bordered" required />
        </label>
        <label class="form-control">
          <span class="label-text">Role</span>
          <select v-model="newUser.role" class="select select-bordered">
            <option value="operator">operator</option>
            <option value="admin">admin</option>
          </select>
        </label>
        <button type="submit" class="btn btn-primary" :disabled="users.loading">Create</button>
      </form>
    </section>
  </div>
</template>
