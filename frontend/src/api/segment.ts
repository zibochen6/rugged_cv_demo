// Segment (click-to-segment) API client.
//
// Minimal contract: one click selects a target, further clicks refine it,
// the mask follows the target in real time; while the target is out of
// view a *ghost* silhouette is kept and the mask resumes automatically
// when it reappears.

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
    let detail = { code: 'UNKNOWN', message: `HTTP ${res.status}` }
    try {
      const json = await res.json()
      if (json.detail) detail = json.detail
    } catch {}
    throw new Error(detail.message || detail.code)
  }

  return res.json()
}

export interface SegmentStatus {
  ok: boolean
  enabled: boolean
  state: 'idle' | 'tracking' | 'lost' | 'loading'
  has_target: boolean
  loading: boolean
  error: string | null
  points: number[][] // [x, y, label] — normalized coordinates
  polygons: number[][][] // each: [[x, y], ...] in 1280x720 canvas space
  ghost_polygons: number[][][]
  center: number[] | null
  infer_ms: number | null
  model_fps: number | null
  frame_id: number | null
  ts: number
  camera_running: boolean
  /** mask quality gate: 'poor' = over-segmented (hand/background included) */
  target_quality?: 'ok' | 'poor'
  /** Chinese hint shown while the mask is over-segmented */
  target_hint?: string | null
  /** user-facing reason when auto-resume/re-lock is gated */
  relock_note?: string | null
  /** identity template exists & target well-segmented -> auto-resume armed */
  resume_armed?: boolean
}

export async function getSegmentStatus(): Promise<SegmentStatus> {
  return fetchJson<SegmentStatus>('/api/segment/status')
}

export interface SegmentActionResult {
  ok: boolean
  state?: string
  message?: string
}

export async function startSegment(): Promise<SegmentActionResult> {
  return fetchJson<SegmentActionResult>('/api/segment/start', { method: 'POST' })
}

export async function stopSegment(): Promise<SegmentActionResult> {
  return fetchJson<SegmentActionResult>('/api/segment/stop', { method: 'POST' })
}

export async function clickSegment(x: number, y: number, label = 1): Promise<SegmentActionResult> {
  return fetchJson<SegmentActionResult>('/api/segment/click', {
    method: 'POST',
    body: JSON.stringify({ x, y, label }),
  })
}

export async function clearSegment(): Promise<SegmentActionResult> {
  return fetchJson<SegmentActionResult>('/api/segment/clear', { method: 'POST' })
}