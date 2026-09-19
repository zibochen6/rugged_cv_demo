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
  camera?: string | null
  camera_slot?: string | null
  health_ok: boolean
  last_error?: string | null
  started_at?: number | null
  uptime_s: number
  metrics: Record<string, any>
  stream_url?: string | null
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
