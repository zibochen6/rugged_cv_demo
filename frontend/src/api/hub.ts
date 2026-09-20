// Visual Hub API client.

const API_BASE = ''

async function fetchJson<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(API_BASE + url, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...options?.headers,
    },
  })
  if (!res.ok) {
    let detail: any = { code: 'UNKNOWN', message: `HTTP ${res.status}` }
    try {
      const json = await res.json()
      if (json.detail) detail = json.detail
    } catch {}
    throw new Error(detail.message || detail.code)
  }
  return res.json()
}

export type ModuleId = 'front' | 'rear' | 'dms'

export interface HubEvent {
  id: number
  ts: number
  source: string
  severity: string
  kind: string
  message: string
  payload?: Record<string, unknown>
}

export interface ModuleStatus {
  id: ModuleId | string
  label: string
  state: string
  pid?: number | null
  port?: number | null
  /** Role label, e.g. "PoE Rear Camera". Kept for backwards compatibility. */
  camera?: string | null
  camera_slot?: string | null
  /** Opaque id of the bound camera; match against `CameraInfo.id`. */
  camera_id?: string | null
  /** Language-neutral label of the bound camera, e.g. "RTSP · 192.168.1.10". */
  camera_label?: string | null
  camera_configured?: boolean
  health_ok: boolean
  last_error?: string | null
  started_at?: number | null
  uptime_s: number
  metrics: Record<string, any>
  stream_url?: string | null
}

export type CameraKind = 'rtsp' | 'usb' | 'file' | 'test'

/**
 * One detected camera.
 *
 * Every field is structured data or a language-neutral scheme token (`RTSP`,
 * `USB`, `video`, …): the backend deliberately ships no prose, so the UI labels
 * options with its own translations. See `backend/app/hub/inventory.py`.
 */
export interface CameraInfo {
  /** Opaque, non-reversible id. The API never returns a raw source. */
  id: string
  kind: CameraKind | string
  /** Credential-free view of the source (RTSP passwords are masked). */
  source: string
  /** Language-neutral label, e.g. "RTSP · 192.168.137.20". */
  label: string
  /** 'usb' | 'config' | 'extra' | 'manual' — where this candidate came from. */
  origin: string
  /** null = not applicable, true/false = reachable / present. */
  reachable: boolean | null
  allowed_modules: string[]
  in_use_by?: string | null
  /** USB topology path, e.g. "1-2.1". Only stable way to tell identical cameras apart. */
  usb_port?: string | null
}

export interface CameraInventory {
  ok: boolean
  cameras: CameraInfo[]
  modules: Record<ModuleId, {
    module: ModuleId | string
    /** Empty when the role has no camera; the UI substitutes its own name. */
    camera_label: string
    camera_id: string | null
    configured: boolean
    /** True when the operator chose this camera, i.e. it is not the env default. */
    overridden: boolean
    allowed_modules: string[]
  }>
}

export interface HubActionResult {
  ok: boolean
  modules: Record<string, ModuleStatus>
  errors: Record<string, { code: string; message: string }>
}

export interface HubStatus {
  ok: boolean
  service: string
  overall: string
  ts: number
  modules: Record<string, ModuleStatus>
  occupancy: Array<Record<string, any>>
  events: HubEvent[]
  operation_mode?: string
  recording?: Record<string, any>
  thermal?: {
    state: 'normal' | 'constrained' | 'critical'
    max_temp_c: number | null
    policy: Record<string, number | boolean>
    degraded_modules: string[]
    reason?: string | null
  }
}

export const getHubStatus = () => fetchJson<HubStatus>('/api/hub/status')
export const getHubEvents = (sinceId = 0) =>
  fetchJson<{ ok: boolean; events: HubEvent[] }>(`/api/hub/events?since_id=${sinceId}&limit=80`)
export const startHubModule = (id: ModuleId) =>
  fetchJson<{ ok: boolean; module: ModuleStatus }>(`/api/hub/modules/${id}/start`, { method: 'POST' })
export const stopHubModule = (id: ModuleId) =>
  fetchJson<{ ok: boolean; module: ModuleStatus }>(`/api/hub/modules/${id}/stop`, { method: 'POST' })
export const restartHubModule = (id: ModuleId) =>
  fetchJson<{ ok: boolean; module: ModuleStatus }>(`/api/hub/modules/${id}/restart`, { method: 'POST' })
export const configHubModule = (id: ModuleId, body: Record<string, unknown>) =>
  fetchJson<{ ok: boolean; config: any }>(`/api/hub/modules/${id}/config`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
export const startAllHubModules = () =>
  fetchJson<HubActionResult>('/api/hub/actions/start-all', { method: 'POST' })
export const stopAllHubModules = () =>
  fetchJson<HubActionResult>('/api/hub/actions/stop-all', { method: 'POST' })
export const getHubStreamUrl = (id: ModuleId) => `${API_BASE}/api/hub/stream/${id}`
export const getHubCameras = () => fetchJson<CameraInventory>('/api/hub/cameras')
/** Classify + redact a hand-entered source. Persists nothing. */
export const probeCamera = (source: string) =>
  fetchJson<{ ok: boolean; camera: CameraInfo }>('/api/hub/cameras/probe', {
    method: 'POST',
    body: JSON.stringify({ source }),
  })
/**
 * Bind a role to a camera. Pass `cameraId` for a detected camera, or `source`
 * for a hand-entered URL / offline clip. A running module is stopped and
 * restarted, so this resolves only once the module is back up.
 */
export const setHubModuleCamera = (id: ModuleId, target: { cameraId?: string; source?: string }) =>
  fetchJson<{ ok: boolean; module: ModuleStatus }>(`/api/hub/modules/${id}/camera`, {
    method: 'PUT',
    body: JSON.stringify(
      target.cameraId ? { camera_id: target.cameraId } : { source: target.source },
    ),
  })
