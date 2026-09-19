import type { ModuleId } from './hub'

const API_BASE = ''

async function fetchJson<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(API_BASE + url, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...options?.headers },
  })
  if (!response.ok) {
    let detail: { code?: string; message?: string } = { message: `HTTP ${response.status}` }
    try {
      const body = await response.json()
      detail = body.detail || detail
    } catch {}
    throw new Error(detail.message || detail.code || `HTTP ${response.status}`)
  }
  return response.json()
}

export type RecordingCameraId = ModuleId

export interface RecordingCameraStatus {
  camera: RecordingCameraId
  state: 'idle' | 'recording' | 'error'
  recording: boolean
  recording_id?: string
  elapsed_s?: number
  size_bytes?: number
  frames_written?: number
  error?: string | null
}

export interface RecordingFile {
  id: string
  camera: RecordingCameraId
  started_at: number
  ended_at?: number | null
  duration_s: number
  size_bytes: number
  reason?: string | null
  state: 'complete' | 'failed'
}

export interface RecordingStatus {
  ok: boolean
  mode: 'inference' | 'recording'
  max_duration_s: number
  storage: { free_bytes: number; total_bytes: number }
  cameras: Record<RecordingCameraId, RecordingCameraStatus>
}

export const getRecordingStatus = () => fetchJson<RecordingStatus>('/api/recording/status')
export const enterRecordingMode = () => fetchJson<RecordingStatus>('/api/recording/mode/enter', { method: 'POST' })
export const exitRecordingMode = () => fetchJson<RecordingStatus>('/api/recording/mode/exit', { method: 'POST' })
export const startRecording = (camera: RecordingCameraId) => fetchJson<RecordingStatus>(`/api/recording/cameras/${camera}/start`, { method: 'POST' })
export const stopRecording = (camera: RecordingCameraId) => fetchJson<RecordingStatus>(`/api/recording/cameras/${camera}/stop`, { method: 'POST' })
export const startAllRecordings = () => fetchJson<RecordingStatus>('/api/recording/actions/start-all', { method: 'POST' })
export const stopAllRecordings = () => fetchJson<RecordingStatus>('/api/recording/actions/stop-all', { method: 'POST' })
export const listRecordings = () => fetchJson<{ ok: boolean; files: RecordingFile[] }>('/api/recording/files')
export const getRecordingStreamUrl = (camera: RecordingCameraId) => `${API_BASE}/api/recording/stream/${camera}`
export const getRecordingPlayUrl = (id: string) => `${API_BASE}/api/recording/files/${encodeURIComponent(id)}/play`
export const getRecordingDownloadUrl = (id: string) => `${API_BASE}/api/recording/files/${encodeURIComponent(id)}/download`
