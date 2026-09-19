import { useCallback, useEffect, useRef } from 'react'
import { clearSegment, clickSegment, type SegmentStatus } from '../api/segment'
import { COPY, type Language } from '../i18n'
import MjpegStream from './MjpegStream'

const W = 1280
const H = 720

function polygon(ctx: CanvasRenderingContext2D, points: number[][], ghost = false) {
  if (points.length < 3) return
  ctx.beginPath()
  ctx.moveTo(points[0][0], points[0][1])
  points.slice(1).forEach(([x, y]) => ctx.lineTo(x, y))
  ctx.closePath()
  ctx.fillStyle = ghost ? 'rgba(255,255,255,.14)' : 'rgba(16,185,129,.38)'
  ctx.strokeStyle = ghost ? '#cbd5e1' : '#34d399'
  ctx.lineWidth = ghost ? 2 : 3
  ctx.setLineDash(ghost ? [10, 8] : [])
  ctx.fill()
  ctx.stroke()
  ctx.setLineDash([])
}

export default function SegmentViewport({
  streamUrl,
  status,
  interactive = true,
  className = '',
  onError,
  language,
}: {
  streamUrl: string
  status: SegmentStatus | null
  interactive?: boolean
  className?: string
  onError?: (message: string) => void
  language: Language
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const copy = COPY[language]

  useEffect(() => {
    const ctx = canvasRef.current?.getContext('2d')
    if (!ctx) return
    ctx.clearRect(0, 0, W, H)
    status?.polygons?.forEach((p) => polygon(ctx, p))
    status?.ghost_polygons?.forEach((p) => polygon(ctx, p, true))
    status?.points?.forEach(([x, y, label]) => {
      ctx.beginPath()
      ctx.arc(x * W, y * H, 9, 0, Math.PI * 2)
      ctx.fillStyle = label === 0 ? '#fb7185' : '#fde047'
      ctx.fill()
      ctx.strokeStyle = '#020617'
      ctx.lineWidth = 2
      ctx.stroke()
    })
    if (status?.center) {
      ctx.beginPath()
      ctx.arc(status.center[0], status.center[1], 5, 0, Math.PI * 2)
      ctx.fillStyle = '#fff'
      ctx.fill()
    }
  }, [status])

  const interact = useCallback(async (event: React.MouseEvent<HTMLDivElement>) => {
    if (!interactive || !status?.enabled) return
    event.preventDefault()
    const rect = event.currentTarget.getBoundingClientRect()
    const x = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width))
    const y = Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height))
    try {
      await clickSegment(x, y, event.button === 2 ? 0 : 1)
    } catch (error) {
      onError?.(error instanceof Error ? error.message : String(error))
    }
  }, [interactive, onError, status?.enabled])

  return (
    <div
      className={`segment-viewport relative overflow-hidden bg-black ${className}`}
      style={{ aspectRatio: '16 / 9', cursor: interactive && status?.enabled ? 'crosshair' : 'zoom-in' }}
      onClick={interact}
      onContextMenu={interact}
    >
      <MjpegStream
        src={streamUrl}
        alt={`Front Segmentation ${copy.liveView}`}
        className="absolute inset-0"
        imageClassName="h-full w-full object-fill"
        loadingLabel={copy.connectingCamera}
      />
      <canvas ref={canvasRef} width={W} height={H} className="pointer-events-none absolute inset-0 h-full w-full" />
      {status?.loading && (
        <div className="absolute inset-0 grid place-items-center bg-black/55 text-sm text-white">{copy.loadingSegmentation}</div>
      )}
      {status?.enabled && (
        <button
          type="button"
          className="absolute bottom-2 right-2 rounded-md border border-white/20 bg-black/65 px-2.5 py-1 text-xs text-white hover:bg-black/85"
          onClick={(event) => {
            event.stopPropagation()
            void clearSegment().catch((error) => onError?.(error instanceof Error ? error.message : String(error)))
          }}
        >
          {copy.clearTarget}
        </button>
      )}
    </div>
  )
}
