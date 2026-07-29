// api/client.ts's envelope interceptor unwraps { payload } so callers never
// see the envelope directly; ApiError models the rejection shape instead of
// bolting properties onto a plain Error.
export class ApiError extends Error {
  code: string | null
  status: number | null
  constructor(message: string, code: string | null, status: number | null) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.status = status
  }
}

// --- backend models (drawbridge/models.py as_dict() shapes) ---
export interface Device {
  // Literal '*' is the "allow all" wildcard entry — see docs/decisions.md.
  serial: string
  mac: string | null
  description: string | null
  // Desired software version (X.X.X). Resolved to an image filename at
  // /api/v1/provision-request time via a ZTPFile lookup, not stored here.
  version: string | null
  config_file: string | null
  added_at: string
  added_by: string | null
}

export interface ProvisioningSession {
  serial: string
  mac: string | null
  ip: string | null
  image: string | null
  config_file: string | null
  // Self-reported via PUT /provision-request/facts once the session is
  // approved — see docs/decisions.md, "Facts-first provisioning". Null
  // until the device reports in, and never copied onto Device.
  model: string | null
  version: string | null
  // Written by drawbridge/queries.py at session creation ('lease_approved')
  // and updated by drawbridge/device_events.py as vendor-specific syslog
  // triggers match (see docs/logging.md, "Multi-vendor"). Stays a loose
  // string, not a literal union of PROVISIONING_STATES: new vendor states
  // can appear without a matching frontend release, and
  // utils/provisioningStates.ts's `??` fallback already renders any
  // unrecognized value gracefully.
  state: string
  approved_at: string
  last_seen_at: string
  // Computed server-side (ProvisioningSession.is_stale()) — the threshold
  // (SESSION_STALE_AFTER_MINUTES) lives once in drawbridge/models.py, not
  // duplicated here as a constant to compare last_seen_at against.
  stale: boolean
}

export interface ProvisioningLog {
  id: number
  serial: string
  // Client/device-supplied via data.get('event', 'provision_complete')
  // (drawbridge/api/leases.py) — not a closed set, stays a string.
  event: string
  image: string | null
  config_file: string | null
  ip: string | null
  timestamp: string
  detail: string | null
}

export interface DeviceLogEntry {
  id: number
  serial: string | null
  source: 'script' | 'syslog'
  message: string
  timestamp: string
}

export interface ZTPFile {
  file_type: FileType
  filename: string
  size_bytes: number
  sha256: string
  // Only ever set for file_type='image' — parsed from the filename at
  // upload or supplied manually, enforced 1:1 with other images.
  version: string | null
  uploaded_at: string
  uploaded_by: string | null
}

// --- auth ---
export interface AuthUser {
  id: number
  username: string
  role: 'admin' | 'operator'
  auth_source: 'local' | 'saml'
}
export interface AdminUser extends AuthUser {
  is_claimed: boolean
  created_at: string
  last_login_at: string | null
}
export type LoginResponse = { must_reset_password: true; username: string } | AuthUser
// Returned once by POST /users and POST /users/<id>/reset-password — never
// re-shown afterward, so it's never added to AdminUser itself.
export type ClaimTokenResponse = AdminUser & { claim_token: string }

// --- frontend-only unions ---
export type FileType = 'image' | 'config'
export type UploadStatus = 'queued' | 'uploading' | 'done' | 'error' | 'canceled'

// --- frontend-only composite shapes ---
export interface QueueItem {
  id: string
  file: File
  fileType: FileType
  filename: string
  sha256?: string
  // image only — see StagedFile.version below.
  version?: string
  // Set when a retry should bypass the version_conflict check and supersede
  // the existing mapping (see stores/files.ts, retryUploadWithReplace).
  replace?: boolean
  // True when this item's error is a version_conflict — lets the UI offer
  // "replace the mapping" instead of just showing a dead-end error.
  conflict?: boolean
  status: UploadStatus
  progress: number
  controller: AbortController | null
  error: string | null
}
export interface StagedFile {
  file: File
  fileType: FileType
  sha256?: string
  // image only — operator override of the version parsed from the
  // filename; left blank to let the backend parse it (POST /files/images).
  version?: string
}

// --- create-payload shapes ---
export type DeviceCreatePayload = Omit<Device, 'added_at' | 'added_by'>
export type DeviceUpdatePayload = Pick<Device, 'mac' | 'description' | 'version' | 'config_file'>
export type UserCreatePayload = Pick<AdminUser, 'username' | 'role'>

// --- axios augmentation for the custom skip401Redirect request flag ---
declare module 'axios' {
  export interface AxiosRequestConfig {
    skip401Redirect?: boolean
  }
}
