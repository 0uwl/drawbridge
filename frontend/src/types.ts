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
  serial: string
  mac: string | null
  description: string | null
  image: string | null
  config_file: string | null
  script: string | null
  added_at: string
  added_by: string | null
}

export interface ProvisioningSession {
  serial: string
  mac: string | null
  ip: string | null
  image: string | null
  config_file: string | null
  // Only ever written as 'lease_approved' today (drawbridge/queries.py).
  // models.py's comment lists future values, but no code transitions state
  // yet — a literal union would misdescribe reality. Widen later once real
  // transitions exist, don't guess them now.
  state: string
  approved_at: string
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
export type FileType = 'image' | 'config' | 'script'
export type UploadStatus = 'queued' | 'uploading' | 'done' | 'error' | 'canceled'

// --- frontend-only composite shapes ---
export interface QueueItem {
  id: string
  file: File
  fileType: FileType
  filename: string
  sha256?: string
  status: UploadStatus
  progress: number
  controller: AbortController | null
  error: string | null
}
export interface StagedFile {
  file: File
  fileType: FileType
  sha256?: string
}

// --- create-payload shapes ---
export type DeviceCreatePayload = Omit<Device, 'added_at' | 'added_by'>
export type UserCreatePayload = Pick<AdminUser, 'username' | 'role'>

// --- axios augmentation for the custom skip401Redirect request flag ---
declare module 'axios' {
  export interface AxiosRequestConfig {
    skip401Redirect?: boolean
  }
}
