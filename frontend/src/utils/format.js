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

// Formats a byte count as a human-readable size (e.g. 1536 -> "1.5 KB").
export function formatBytes(bytes) {
  if (bytes === null || bytes === undefined) return null
  if (bytes === 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  const exponent = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1)
  const value = bytes / 1024 ** exponent
  return `${exponent === 0 ? value : value.toFixed(1)} ${units[exponent]}`
}
