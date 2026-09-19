import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { flushSync } from 'react-dom'
import { Gauge, Maximize2 } from 'lucide-react'
import MjpegStream from '../components/MjpegStream'
import SegmentViewport from '../components/SegmentViewport'
import FullscreenMonitor from '../components/FullscreenMonitor'
import { getSegmentStatus, type SegmentStatus } from '../api/segment'
import { COPY, errorText, eventText, moduleCopy, stateText, type Language } from '../i18n'
import {
  configHubModule,
  getHubStatus,
  getHubStreamUrl,
  restartHubModule,
  startAllHubModules,
  startHubModule,
  stopAllHubModules,
  stopHubModule,
  type HubEvent,
  type HubStatus,
  type ModuleId,
  type ModuleStatus,
} from '../api/hub'

const MODULES: ModuleId[] = ['front', 'rear', 'dms']

function live(state?: string) {
  return ['running', 'degraded', 'starting'].includes(state || '')
}

function dot(state?: string) {
  if (state === 'running') return 'indicator-live'
  if (state === 'starting' || state === 'degraded') return 'indicator-warn'
  if (state === 'error') return 'indicator-error'
  return 'indicator-stopped'
}

function number(value: unknown, digits = 1) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed.toFixed(digits) : '--'
}

function metrics(module?: ModuleStatus) {
  const m = module?.metrics || {}
  const capture = number(m.capture_fps ?? m.fps)
  const infer = number(m.inference_fps ?? m.model_fps ?? m.fps)
  const age = m.frame_age_s == null ? '--' : `${number(m.frame_age_s, 2)}s`
  return { capture, infer, age }
}

function VideoPane({ id, module, segment, language, onExpand, onError, suspendStream }: {
  id: ModuleId
  module?: ModuleStatus
  segment: SegmentStatus | null
  language: Language
  onExpand: () => void
  onError: (message: string) => void
  suspendStream: boolean
}) {
  const active = live(module?.state)
  const url = getHubStreamUrl(id)
  const copy = COPY[language]
  const localized = moduleCopy(language, id)
  return (
    <div className="group relative">
      {!active || suspendStream ? (
        <div className="grid aspect-video place-items-center rounded-lg bg-black text-sm text-slate-500">{copy.waitingToStart}</div>
      ) : id === 'front' ? (
        <SegmentViewport streamUrl={url} status={segment} language={language} onError={onError} className="rounded-lg" />
      ) : (
        <MjpegStream
          src={url}
          alt={`${localized.label} ${copy.liveView}`}
          className="aspect-video w-full rounded-lg"
          imageClassName="h-full w-full object-contain"
          loadingLabel={copy.connectingCamera}
        />
      )}
      <button
        type="button"
        className="icon-button absolute right-2 top-2 bg-black/75 text-white"
        onClick={onExpand}
        disabled={!active}
        aria-label={`${copy.fullscreen}: ${localized.label}`}
        title={`${copy.fullscreen}: ${localized.label}`}
      >
        <Maximize2 aria-hidden="true" size={19} strokeWidth={2} />
      </button>
    </div>
  )
}

function ModuleCard({ id, module, segment, language, busy, onRun, onExpand, onError, suspendStream, showMetrics }: {
  id: ModuleId
  module?: ModuleStatus
  segment: SegmentStatus | null
  language: Language
  busy: boolean
  onRun: (fn: () => Promise<unknown>) => void
  onExpand: () => void
  onError: (message: string) => void
  suspendStream: boolean
  showMetrics: boolean
}) {
  const active = live(module?.state)
  const m = metrics(module)
  const copy = COPY[language]
  const localized = moduleCopy(language, id)
  return (
    <section className="camera-card">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <span className={`indicator ${dot(module?.state)}`} />
            <h2 className="font-semibold">{localized.label}</h2>
          </div>
          <p className="mt-1 text-xs text-muted">{localized.camera} · {stateText(language, module?.state)}</p>
        </div>
        <span className="rounded-full bg-slate-900 px-2.5 py-1 text-[11px] text-slate-300">{module?.health_ok ? copy.linkHealthy : copy.notReady}</span>
      </div>
      <VideoPane id={id} module={module} segment={segment} language={language} onExpand={onExpand} onError={onError} suspendStream={suspendStream} />
      {showMetrics && (
        <div className="mt-3 grid grid-cols-3 gap-2">
          <div className="metric"><span>{copy.capture}</span><strong>{m.capture} FPS</strong></div>
          <div className="metric"><span>{copy.inference}</span><strong>{m.infer} FPS</strong></div>
          <div className="metric"><span>{copy.frameAge}</span><strong>{m.age}</strong></div>
        </div>
      )}
      {module?.last_error && <div className="mt-3 rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-200">{module.last_error}</div>}
      <div className="mt-3 flex gap-2">
        <button className="btn-success flex-1 text-sm" disabled={busy || active} onClick={() => onRun(() => startHubModule(id))}>{copy.start}</button>
        <button className="btn-danger flex-1 text-sm" disabled={busy || !active} onClick={() => onRun(() => stopHubModule(id))}>{copy.stop}</button>
        <button className="btn-secondary text-sm" disabled={busy} onClick={() => onRun(() => restartHubModule(id))}>{copy.restart}</button>
      </div>
    </section>
  )
}

function Toggle({ checked, disabled, label, onChange }: {
  checked: boolean
  disabled?: boolean
  label: string
  onChange: (checked: boolean) => void
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      className={`switch ${checked ? 'switch-on' : ''}`}
      onClick={() => onChange(!checked)}
    >
      <span className="switch-thumb" />
    </button>
  )
}

interface RearConfig {
  danger: number
  warning: number
  buzzer: boolean
}

function readRearConfig(snapshot: HubStatus, fallback: RearConfig): RearConfig {
  const metrics = snapshot.modules.rear?.metrics || {}
  return {
    danger: metrics.danger_m == null ? fallback.danger : Number(metrics.danger_m),
    warning: metrics.warning_m == null ? fallback.warning : Number(metrics.warning_m),
    buzzer: metrics.buzzer == null ? fallback.buzzer : Boolean(metrics.buzzer),
  }
}

export default function HubPage({ language }: { language: Language }) {
  const [status, setStatus] = useState<HubStatus | null>(null)
  const [segment, setSegment] = useState<SegmentStatus | null>(null)
  const [expanded, setExpanded] = useState<ModuleId | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [danger, setDanger] = useState(1.5)
  const [warning, setWarning] = useState(3.0)
  const [buzzer, setBuzzer] = useState(false)
  const [fatigue, setFatigue] = useState(true)
  const [fatigueAlarmBuzzer, setFatigueAlarmBuzzer] = useState(false)
  const [helmet, setHelmet] = useState(true)
  const [showMetrics, setShowMetrics] = useState(true)
  const rearDirty = useRef(false)
  const rearConfirmed = useRef<RearConfig>({ danger: 1.5, warning: 3.0, buzzer: false })
  const monitorRef = useRef<HTMLDivElement>(null)
  const fullscreenActive = useRef(false)
  const copy = COPY[language]

  const refresh = useCallback(async () => {
    try {
      const snap = await getHubStatus()
      setStatus(snap)
      if (!rearDirty.current) {
        const rear = readRearConfig(snap, rearConfirmed.current)
        rearConfirmed.current = rear
        setDanger(rear.danger)
        setWarning(rear.warning)
        setBuzzer(rear.buzzer)
      }
      const dms = snap.modules.dms?.metrics?.dms || {}
      if (dms.fatigue?.enabled != null) setFatigue(Boolean(dms.fatigue.enabled))
      if (dms.fatigue?.alarm_buzzer != null) setFatigueAlarmBuzzer(Boolean(dms.fatigue.alarm_buzzer))
      if (dms.helmet?.enabled != null) setHelmet(Boolean(dms.helmet.enabled))
    } catch (reason) {
      setError(errorText(language, reason))
    }
  }, [language])

  useEffect(() => {
    void refresh()
    const timer = window.setInterval(() => void refresh(), 1200)
    return () => window.clearInterval(timer)
  }, [refresh])

  useEffect(() => {
    if (!live(status?.modules.front?.state)) {
      setSegment(null)
      return
    }
    const poll = () => getSegmentStatus().then(setSegment).catch(() => undefined)
    void poll()
    const timer = window.setInterval(poll, 250)
    return () => window.clearInterval(timer)
  }, [status?.modules.front?.state])

  const closeMonitor = useCallback(() => {
    const exiting = document.fullscreenElement ? document.exitFullscreen() : Promise.resolve()
    void exiting.catch(() => undefined).finally(() => {
      fullscreenActive.current = false
      setExpanded(null)
    })
  }, [])

  const openMonitor = useCallback((id: ModuleId) => {
    flushSync(() => setExpanded(id))
    const target = monitorRef.current
    if (!target?.requestFullscreen) return
    fullscreenActive.current = true
    void target.requestFullscreen().catch(() => {
      fullscreenActive.current = false
    })
  }, [])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && expanded && !document.fullscreenElement) {
        setExpanded(null)
      }
    }
    const onFullscreenChange = () => {
      if (document.fullscreenElement) {
        fullscreenActive.current = true
      } else if (fullscreenActive.current || expanded) {
        fullscreenActive.current = false
        setExpanded(null)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    document.addEventListener('fullscreenchange', onFullscreenChange)
    return () => {
      window.removeEventListener('keydown', onKeyDown)
      document.removeEventListener('fullscreenchange', onFullscreenChange)
    }
  }, [expanded])

  const run = useCallback(async (key: string, fn: () => Promise<unknown>) => {
    setBusy(key)
    setError(null)
    try {
      const result = await fn() as { errors?: Record<string, { message: string }> }
      if (result?.errors && Object.keys(result.errors).length) {
        setError(Object.entries(result.errors).map(([id, value]) => `${id}: ${errorText(language, value.message)}`).join('; '))
      }
      await refresh()
    } catch (reason) {
      setError(errorText(language, reason))
    } finally {
      setBusy(null)
    }
  }, [language, refresh])

  const saveRearConfig = useCallback(async (next: RearConfig) => {
    if (next.danger >= next.warning) {
      setError(copy.invalidThresholds)
      return
    }
    rearDirty.current = true
    setBusy('rear-config')
    setError(null)
    try {
      await configHubModule('rear', {
        danger_m: next.danger,
        warning_m: next.warning,
        buzzer: next.buzzer,
      })
      const snap = await getHubStatus()
      setStatus(snap)
      const confirmed = readRearConfig(snap, next)
      rearConfirmed.current = confirmed
      setDanger(confirmed.danger)
      setWarning(confirmed.warning)
      setBuzzer(confirmed.buzzer)
    } catch (reason) {
      const confirmed = rearConfirmed.current
      setDanger(confirmed.danger)
      setWarning(confirmed.warning)
      setBuzzer(confirmed.buzzer)
      setError(`${copy.configFailed} ${errorText(language, reason)}`)
    } finally {
      rearDirty.current = false
      setBusy(null)
    }
  }, [copy.configFailed, copy.invalidThresholds, language])

  const events = useMemo<HubEvent[]>(() => [...(status?.events || [])].reverse(), [status])
  const activeCount = MODULES.filter((id) => live(status?.modules[id]?.state)).length
  const expandedModule = expanded ? status?.modules[expanded] : undefined
  const thermal = status?.thermal

  return (
    <div className="space-y-4">
      <section className="card flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
        <div>
          <div className="flex items-center gap-3"><h2 className="text-xl font-bold">{copy.situationTitle}</h2><span className="text-sm text-muted">{activeCount}/3 {copy.channelsRunning}</span></div>
          <p className="mt-1 text-sm text-muted">{copy.safetyPriority}</p>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            className={`icon-button ${showMetrics ? '' : 'opacity-60'}`}
            onClick={() => setShowMetrics((value) => !value)}
            aria-label={showMetrics ? copy.hidePerformance : copy.showPerformance}
            title={showMetrics ? copy.hidePerformance : copy.showPerformance}
          >
            <Gauge aria-hidden="true" size={19} strokeWidth={2} />
          </button>
          <button className="btn-primary" disabled={busy != null} onClick={() => void run('all', startAllHubModules)}>{copy.startAll}</button>
          <button className="btn-danger" disabled={busy != null} onClick={() => void run('all', stopAllHubModules)}>{copy.stopAll}</button>
        </div>
      </section>

      {error && <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-200">{error}</div>}

      {thermal && thermal.state !== 'normal' && (
        <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-100">
          {copy.thermalState}: {thermal.state === 'critical' ? copy.criticalDegradation : copy.constrainedOperation} · {number(thermal.max_temp_c)}C{thermal.reason && ` · ${language === 'en' ? errorText(language, thermal.reason) : thermal.reason}`}
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        {MODULES.map((id) => <ModuleCard key={id} id={id} module={status?.modules[id]} segment={segment} language={language} busy={busy === id || busy === 'all'} onRun={(fn) => void run(id, fn)} onExpand={() => openMonitor(id)} onError={(message) => setError(errorText(language, message))} suspendStream={expanded === id} showMetrics={showMetrics} />)}
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <section className="card space-y-4">
          <div><h3 className="font-semibold">{copy.rearConfigTitle}</h3><p className="mt-1 text-xs text-muted">{copy.rearConfigHelp}</p></div>
          <label className="control-row"><span>{copy.dangerDistance}</span><input className="range" type="range" min="0.3" max="5" step="0.1" value={danger} aria-valuetext={`${danger.toFixed(1)} m`} onPointerDown={() => { rearDirty.current = true }} onChange={(event) => { rearDirty.current = true; setDanger(Number(event.target.value)) }} /><strong>{danger.toFixed(1)}m</strong></label>
          <label className="control-row"><span>{copy.warningDistance}</span><input className="range" type="range" min="0.5" max="10" step="0.1" value={warning} aria-valuetext={`${warning.toFixed(1)} m`} onPointerDown={() => { rearDirty.current = true }} onChange={(event) => { rearDirty.current = true; setWarning(Number(event.target.value)) }} /><strong>{warning.toFixed(1)}m</strong></label>
          <label className="control-row"><span>{copy.buzzer}</span><Toggle checked={buzzer} disabled={!live(status?.modules.rear?.state) || busy != null || danger >= warning} label={copy.buzzer} onChange={(next) => {
            rearDirty.current = true
            setBuzzer(next)
            void saveRearConfig({ danger, warning, buzzer: next })
          }} /></label>
          <button className="btn-primary w-full" disabled={!live(status?.modules.rear?.state) || busy != null || danger >= warning} onClick={() => void saveRearConfig({ danger, warning, buzzer })}>{busy === 'rear-config' ? copy.saving : copy.saveApply}</button>
          {danger >= warning && <p className="text-xs text-red-300">{copy.invalidThresholds}</p>}
          {!live(status?.modules.rear?.state) && <p className="text-xs text-amber-200">{copy.rearMustRun}</p>}
        </section>

        <section className="card space-y-4">
          <div><h3 className="font-semibold">{copy.cabinDetection}</h3><p className="mt-1 text-xs text-muted">{copy.cabinHelp}</p></div>
          <label className="control-row"><span>{copy.fatigueDetection}</span><Toggle checked={fatigue} disabled={!live(status?.modules.dms?.state) || busy != null} label={copy.fatigueDetection} onChange={(next) => {
            setFatigue(next)
            void run('dms-config', () => configHubModule('dms', { fatigue_enabled: next }))
          }} /></label>
          <label className="control-row"><span>{copy.fatigueAlarmBuzzer}</span><Toggle checked={fatigueAlarmBuzzer} disabled={!live(status?.modules.dms?.state) || busy != null} label={copy.fatigueAlarmBuzzer} onChange={(next) => {
            setFatigueAlarmBuzzer(next)
            void run('dms-config', () => configHubModule('dms', { fatigue_alarm_buzzer: next }))
          }} /></label>
          <label className="control-row"><span>{copy.helmetDetection}</span><Toggle checked={helmet} disabled={!live(status?.modules.dms?.state) || busy != null} label={copy.helmetDetection} onChange={(next) => {
            setHelmet(next)
            void run('dms-config', () => configHubModule('dms', { helmet_enabled: next }))
          }} /></label>
          <div className="rounded-lg bg-slate-950/60 p-3 text-xs text-muted">{copy.cabinNote}</div>
        </section>

        <section className="card">
          <h3 className="mb-3 font-semibold">{copy.eventStream}</h3>
          <div className="max-h-72 space-y-2 overflow-auto text-xs">
            {!events.length && <div className="text-muted">{copy.noEvents}</div>}
            {events.map((event) => <div key={event.id} className="grid grid-cols-[64px_44px_1fr] gap-2 border-b border-slate-700/50 pb-2"><span className="text-muted">{new Date(event.ts * 1000).toLocaleTimeString(language === 'zh' ? 'zh-CN' : 'en-US')}</span><span className="uppercase text-slate-400">{event.source}</span><span className={event.severity === 'danger' || event.severity === 'error' ? 'text-red-300' : event.severity === 'warning' ? 'text-amber-300' : 'text-slate-200'}>{eventText(language, event)}</span></div>)}
          </div>
        </section>
      </div>

      {expanded && expandedModule && live(expandedModule.state) && (
        <FullscreenMonitor ref={monitorRef} id={expanded} module={expandedModule} segment={segment} language={language} onClose={closeMonitor} onError={(message) => setError(errorText(language, message))} showMetrics={showMetrics} />
      )}
    </div>
  )
}
