// Session states are produced by drawbridge/device_events.py (Cisco today,
// Juniper planned — see docs/logging.md, "Multi-vendor") and aren't a
// closed set from the frontend's perspective: new vendor states can appear
// without a matching frontend release. Stays a plain Record with a `??`
// fallback, not an exhaustive union — deliberately different from
// TYPE_LABELS (fileTypes.ts), whose FileType union IS closed.
export const STATE_BADGES: Record<string, string> = {
  lease_approved: 'badge-ghost',
  script_fetched: 'badge-info',
  downloading: 'badge-info',
  updating_software: 'badge-warning',
  rebooting: 'badge-warning',
  configuring: 'badge-info',
  error: 'badge-error',
}

export function stateBadgeClass(state: string): string {
  return STATE_BADGES[state] ?? 'badge-neutral'
}

// No state->label map to maintain: unlike badge color (which needs
// explicit intent per state), "snake_case -> Title case" needs none, so
// it already handles any future/unrecognized value for free.
export function formatStateLabel(state: string): string {
  return state.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase())
}
