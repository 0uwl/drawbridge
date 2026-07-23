import { onMounted, onUnmounted } from 'vue'

// Plain interval polling, not push (WebSockets/SSE) — Drawbridge's update
// frequency (device state changes, log lines) is on the order of seconds,
// not sub-second, so a poll interval is indistinguishable from "live" for
// a human watching the page, without the server-side broadcast machinery
// push would need across Gunicorn workers (see docs/database.md,
// "Concurrency under multiple Gunicorn workers" — Postgres deployments run
// more than one). Pauses while the tab isn't visible so a forgotten
// background tab doesn't poll forever.
export function usePolling(callback: () => void, intervalMs: number): void {
  let timer: ReturnType<typeof setInterval> | null = null

  function start(): void {
    if (timer !== null) return
    callback()
    timer = setInterval(callback, intervalMs)
  }

  function stop(): void {
    if (timer === null) return
    clearInterval(timer)
    timer = null
  }

  function handleVisibilityChange(): void {
    if (document.hidden) {
      stop()
    } else {
      start()
    }
  }

  onMounted(() => {
    start()
    document.addEventListener('visibilitychange', handleVisibilityChange)
  })

  onUnmounted(() => {
    stop()
    document.removeEventListener('visibilitychange', handleVisibilityChange)
  })
}
