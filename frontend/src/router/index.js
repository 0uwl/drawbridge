import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/devices' },
    { path: '/login', name: 'login', component: () => import('../views/Login.vue') },
    {
      path: '/devices',
      name: 'devices',
      component: () => import('../views/Devices.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/log',
      name: 'log',
      component: () => import('../views/Log.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/settings',
      name: 'settings',
      component: () => import('../views/Settings.vue'),
      meta: { requiresAuth: true },
    },
    { path: '/:pathMatch(.*)*', redirect: '/devices' },
  ],
})

// Pinia state doesn't survive a hard refresh, so on the first navigation
// after a reload we probe for an existing session cookie via fetchMe()
// before deciding whether to redirect — otherwise every reload of an
// authenticated page would bounce to /login even with a valid session.
let attemptedFetch = false

router.beforeEach(async (to) => {
  const auth = useAuthStore()

  if (auth.currentUser === null && !attemptedFetch) {
    attemptedFetch = true
    await auth.fetchMe()
  }

  if (to.meta.requiresAuth && !auth.currentUser) {
    return '/login'
  }

  if (to.path === '/login' && auth.currentUser) {
    return '/devices'
  }

  return true
})

export default router
