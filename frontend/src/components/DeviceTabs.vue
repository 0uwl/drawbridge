<script setup lang="ts">
import { useRoute } from 'vue-router'

const route = useRoute()
</script>

<template>
  <div role="tablist" class="tabs tabs-boxed mb-4 w-fit">
    <router-link to="/sessions" role="tab" class="tab" :class="{ 'tab-active': route.path === '/sessions' }">
      Active Sessions
    </router-link>
    <router-link to="/devices" role="tab" class="tab" :class="{ 'tab-active': route.path === '/devices' }">
      Allowlist
    </router-link>
  </div>
</template>
