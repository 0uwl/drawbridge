<script setup>
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from './stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

async function logout() {
  await auth.logout()
  router.push('/login')
}
</script>

<template>
  <div v-if="route.path !== '/login'" class="navbar bg-base-100 shadow-sm px-6">
    <div class="flex-1">
      <span class="text-xl font-bold">Drawbridge</span>
    </div>
    <div class="flex-none gap-2">
      <router-link to="/sessions" class="btn btn-ghost btn-sm">Devices</router-link>
      <router-link to="/log" class="btn btn-ghost btn-sm">Log</router-link>
      <router-link to="/settings" class="btn btn-ghost btn-sm">Settings</router-link>
      <span v-if="auth.currentUser" class="text-sm text-base-content/60 ml-2">{{ auth.currentUser.username }}</span>
      <button class="btn btn-ghost btn-sm" @click="logout">Logout</button>
    </div>
  </div>

  <router-view />
</template>
