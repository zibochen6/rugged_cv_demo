import { forwardRef } from 'react'
import { ArrowLeft } from 'lucide-react'
import MjpegStream from './MjpegStream'
import SegmentViewport from './SegmentViewport'
import { getHubStreamUrl, type ModuleId, type ModuleStatus } from '../api/hub'
import type { SegmentStatus } from '../api/segment'
import { COPY, moduleCopy, type Language } from '../i18n'

interface FullscreenMonitorProps {
  id: ModuleId
  module: ModuleStatus
  segment: SegmentStatus | null
  onClose: () => void
  onError: (message: string) => void
  language: Language
  showMetrics: boolean
}

const FullscreenMonitor = forwardRef<HTMLDivElement, FullscreenMonitorProps>(
  function FullscreenMonitor({ id, module, segment, onClose, onError, language, showMetrics }, ref) {
    const metrics = module.metrics || {}
    const copy = COPY[language]
    const localized = moduleCopy(language, id)
    return (
      <div ref={ref} className="fullscreen-monitor">
        <header className="fullscreen-monitor__bar">
          <button
            type="button"
            className="icon-button"
            aria-label={copy.backToHub}
            title={copy.backToHub}
            onClick={onClose}
          >
            <ArrowLeft aria-hidden="true" size={22} strokeWidth={2} />
          </button>
          <div className="min-w-0">
            <h2 className="truncate text-lg font-semibold">{localized.label}</h2>
            <p className="truncate text-xs text-muted">
              {module.camera_label || localized.camera} · {copy.inferenceContinues} · {copy.escToReturn}
            </p>
          </div>
          {showMetrics && (
            <div className="ml-auto hidden items-center gap-4 text-xs text-slate-300 sm:flex">
              <span>{copy.capture} {formatMetric(metrics.capture_fps ?? metrics.fps)} FPS</span>
              <span>{copy.inference} {formatMetric(metrics.inference_fps ?? metrics.model_fps ?? metrics.fps)} FPS</span>
            </div>
          )}
        </header>
        <main className="fullscreen-monitor__stage">
          {id === 'front' ? (
            <SegmentViewport
              streamUrl={getHubStreamUrl('front')}
              status={segment}
              onError={onError}
              language={language}
              className="fullscreen-monitor__video"
            />
          ) : (
            <MjpegStream
              src={getHubStreamUrl(id)}
              alt={`${localized.label} ${copy.liveView}`}
              className="h-full w-full"
              imageClassName="fullscreen-monitor__image"
              loadingLabel={copy.connectingCamera}
            />
          )}
        </main>
      </div>
    )
  },
)

function formatMetric(value: unknown) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed.toFixed(1) : '--'
}

export default FullscreenMonitor
