// Abbreviates an ISO 8601 timestamp (as returned by drawbridge/models.py's
// utcnow_iso()) to date + time-to-the-minute in the browser's local
// timezone. Callers that need full precision should keep the raw ISO
// string available too, e.g. via a `title` tooltip.
export function formatTimestamp(iso) {
  if (!iso) return null
  return new Date(iso).toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}
