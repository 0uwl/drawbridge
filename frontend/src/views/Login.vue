<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const router = useRouter()

const mode = ref<'login' | 'claim' | 'reset'>('login')
const username = ref('')
const password = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const claimToken = ref('')

async function submit(): Promise<void> {
  if (mode.value === 'reset') {
    if (newPassword.value !== confirmPassword.value) {
      auth.error = 'Passwords do not match'
      return
    }
    const ok = await auth.resetPassword(username.value, password.value, newPassword.value)
    if (ok) router.push('/devices')
    return
  }

  if (mode.value === 'claim' && password.value !== confirmPassword.value) {
    auth.error = 'Passwords do not match'
    return
  }

  const ok =
    mode.value === 'login'
      ? await auth.login(username.value, password.value)
      : await auth.claim(username.value, password.value, claimToken.value)

  if (!ok) return

  if (mode.value === 'login') {
    if (auth.pendingReset) {
      mode.value = 'reset'
      return
    }
    router.push('/devices')
  } else {
    mode.value = 'login'
    password.value = ''
    confirmPassword.value = ''
    claimToken.value = ''
  }
}
</script>

<template>
  <div class="min-h-screen flex items-center justify-center bg-base-200">
    <div class="card w-full max-w-sm bg-base-100 shadow-xl">
      <div class="card-body">
        <h1 class="card-title">Drawbridge</h1>

        <div v-if="mode !== 'reset'" class="tabs tabs-boxed mb-2">
          <a class="tab" :class="{ 'tab-active': mode === 'login' }" @click="mode = 'login'">Log in</a>
          <a class="tab" :class="{ 'tab-active': mode === 'claim' }" @click="mode = 'claim'">Claim account</a>
        </div>
        <div v-else class="alert alert-warning text-sm mb-2">Password reset required before continuing.</div>

        <form @submit.prevent="submit" class="flex flex-col gap-3">
          <label v-if="mode !== 'reset'" class="form-control">
            <span class="label-text">Username</span>
            <input v-model="username" type="text" class="input input-bordered" required />
          </label>
          <label v-if="mode === 'claim'" class="form-control">
            <span class="label-text">Claim token</span>
            <input v-model="claimToken" type="text" class="input input-bordered" required />
          </label>
          <label v-if="mode !== 'reset'" class="form-control">
            <span class="label-text">{{ mode === 'login' ? 'Password' : 'New password' }}</span>
            <input
              v-model="password"
              type="password"
              class="input input-bordered"
              required
              :minlength="mode === 'claim' ? 8 : undefined"
            />
          </label>
          <label v-if="mode === 'reset'" class="form-control">
            <span class="label-text">New password</span>
            <input v-model="newPassword" type="password" class="input input-bordered" required minlength="8" />
          </label>
          <label v-if="mode !== 'login'" class="form-control">
            <span class="label-text">Confirm password</span>
            <input v-model="confirmPassword" type="password" class="input input-bordered" required minlength="8" />
          </label>

          <div v-if="auth.error" class="alert alert-error text-sm">{{ auth.error }}</div>

          <button type="submit" class="btn btn-primary mt-2" :class="{ loading: auth.loading }" :disabled="auth.loading">
            {{ mode === 'login' ? 'Log in' : mode === 'claim' ? 'Set password' : 'Set new password' }}
          </button>
        </form>
      </div>
    </div>
  </div>
</template>
