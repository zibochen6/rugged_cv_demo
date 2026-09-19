import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Download, Maximize2, Play, Square, Video, X } from 'lucide-react'
import MjpegStream from '../components/MjpegStream'
import { COPY, errorText, type Language } from '../i18n'
import {
  enterRecordingMode,
  getRecordingDownloadUrl,
  getRecordingPlayUrl,
  getRecordingStatus,
  getRecordingStreamUrl,
  listRecordings,
  startAllRecordings,
  startRecording,
  stopAllRecordings,
  stopRecording,
  type RecordingCameraId,
  type RecordingFile,
  type RecordingStatus,
} from '../api/recording'

const CAMERAS: RecordingCameraId[] = ['front', 'rear', 'dms']

function formatDuration(value?: number) {
  const seconds = Math.max(0, Math.floor(value || 0))
  const minutes = Math.floor(seconds / 60)
  return `${String(minutes).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`
}

function formatBytes(value?: number) {
  const bytes = Math.max(0, value || 0)
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`
}

function cameraName(id: RecordingCameraId, language: Language) {
  const copy = COPY[language]
  return id === 'front' ? copy.rawFrontCamera : id === 'rear' ? copy.rawRearCamera : copy.rawCabinCamera
}

function CameraCard({ camera, status, language, busy, onStart, onStop, onExpand }: {
  camera: RecordingCameraId
  status: RecordingStatus | null
  language: Language
  busy: boolean
  onStart: () => void
  onStop: () => void
  onExpand: () => void
}) {
  const copy = COPY[language]
  const item = status?.cameras[camera]
  const recording = Boolean(item?.recording)
  return (
    <section className="camera-card">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <span className={`indicator ${recording ? 'indicator-live' : item?.state === 'error' ? 'indicator-error' : 'indicator-stopped'}`} />
            <h2 className="font-semibold">{cameraName(camera, language)}</h2>
          </div>
          <p className="mt-1 text-xs text-muted">{recording ? copy.recordingActive : copy.waitingToStart}</p>
        </div>
        {recording && <span className="rounded-full bg-red-500/15 px-2.5 py-1 text-[11px] text-red-200">REC</span>}
      </div>
      <div className="group relative grid aspect-video place-items-center overflow-hidden rounded-lg bg-black">
        {recording ? (
          <MjpegStream
            className="h-full w-full"
            imageClassName="h-full w-full object-contain"
            src={getRecordingStreamUrl(camera)}
            alt={`${cameraName(camera, language)} ${copy.recordingPreview}`}
            loadingLabel={copy.connectingCamera}
          />
        ) : <span className="px-4 text-center text-sm text-slate-500">{copy.recordingIdle}</span>}
        <button
          type="button"
          className="icon-button absolute right-2 top-2 bg-black/75 text-white"
          disabled={!recording}
          onClick={onExpand}
          aria-label={`${copy.fullscreen}: ${cameraName(camera, language)}`}
          title={`${copy.fullscreen}: ${cameraName(camera, language)}`}
        ><Maximize2 aria-hidden="true" size={19} /></button>
      </div>
      <div className="mt-3 grid grid-cols-3 gap-2">
        <div className="metric"><span>{copy.recordingElapsed}</span><strong>{formatDuration(item?.elapsed_s)}</strong></div>
        <div className="metric"><span>{copy.recordingSize}</span><strong>{formatBytes(item?.size_bytes)}</strong></div>
        <div className="metric"><span>{copy.recordingFrames}</span><strong>{item?.frames_written ?? '--'}</strong></div>
      </div>
      {item?.error && <p className="mt-3 rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-200">{item.error}</p>}
      <div className="mt-3 flex gap-2">
        <button className="btn-success flex-1 inline-flex items-center justify-center gap-2 text-sm" disabled={busy || recording} onClick={onStart}><Video aria-hidden="true" size={17} />{copy.startRecording}</button>
        <button className="btn-danger flex-1 inline-flex items-center justify-center gap-2 text-sm" disabled={busy || !recording} onClick={onStop}><Square aria-hidden="true" size={16} fill="currentColor" />{copy.stopRecording}</button>
      </div>
    </section>
  )
}

function RecordingMonitor({ camera, language, onClose }: { camera: RecordingCameraId; language: Language; onClose: () => void }) {
  const root = useRef<HTMLDivElement>(null)
  const copy = COPY[language]
  useEffect(() => {
    void root.current?.requestFullscreen?.().catch(() => undefined)
    const change = () => { if (!document.fullscreenElement) onClose() }
    document.addEventListener('fullscreenchange', change)
    return () => document.removeEventListener('fullscreenchange', change)
  }, [onClose])
  const close = () => {
    const exiting = document.fullscreenElement ? document.exitFullscreen() : Promise.resolve()
    void exiting.finally(onClose)
  }
  return <div ref={root} className="fullscreen-monitor">
    <header className="fullscreen-monitor__bar">
      <button type="button" className="icon-button" onClick={close} aria-label={copy.backToHub} title={copy.backToHub}><X aria-hidden="true" size={22} /></button>
      <div><h2 className="text-lg font-semibold">{cameraName(camera, language)}</h2><p className="text-xs text-muted">{copy.recordingPreview} · {copy.escToReturn}</p></div>
    </header>
    <main className="fullscreen-monitor__stage"><MjpegStream className="h-full w-full" imageClassName="fullscreen-monitor__image" src={getRecordingStreamUrl(camera)} alt={`${cameraName(camera, language)} ${copy.recordingPreview}`} loadingLabel={copy.connectingCamera} /></main>
  </div>
}

export default function RecordingPage({ language, onExit }: { language: Language; onExit: () => Promise<void> }) {
  const [status, setStatus] = useState<RecordingStatus | null>(null)
  const [files, setFiles] = useState<RecordingFile[]>([])
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<RecordingCameraId | null>(null)
  const [playing, setPlaying] = useState<RecordingFile | null>(null)
  const copy = COPY[language]

  const refresh = useCallback(async () => {
    const [nextStatus, catalog] = await Promise.all([getRecordingStatus(), listRecordings()])
    setStatus(nextStatus)
    setFiles(catalog.files.filter((file) => file.state === 'complete'))
  }, [])

  useEffect(() => {
    void refresh().catch((reason) => setError(errorText(language, reason)))
    const timer = window.setInterval(() => void refresh().catch(() => undefined), 1000)
    return () => window.clearInterval(timer)
  }, [language, refresh])

  const run = useCallback(async (key: string, action: () => Promise<unknown>) => {
    setBusy(key)
    setError(null)
    try { await action(); await refresh() } catch (reason) { setError(errorText(language, reason)) } finally { setBusy(null) }
  }, [language, refresh])

  const activeCount = useMemo(() => CAMERAS.filter((camera) => status?.cameras[camera]?.recording).length, [status])

  return <div className="space-y-4">
    <section className="card flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
      <div><div className="flex items-center gap-3"><h2 className="text-xl font-bold">{copy.recordingTitle}</h2><span className="text-sm text-muted">{activeCount}/3 REC</span></div><p className="mt-1 text-sm text-muted">{copy.recordingHelp}</p></div>
      <div className="flex flex-wrap gap-2">
        <button className="btn-success inline-flex items-center gap-2" disabled={busy !== null} onClick={() => void run('all-start', startAllRecordings)}><Video aria-hidden="true" size={17} />{copy.startAllRecordings}</button>
        <button className="btn-danger inline-flex items-center gap-2" disabled={busy !== null || activeCount === 0} onClick={() => void run('all-stop', stopAllRecordings)}><Square aria-hidden="true" size={16} fill="currentColor" />{copy.stopAllRecordings}</button>
        <button className="btn-secondary" disabled={busy !== null || activeCount > 0} onClick={() => void run('exit', onExit)}>{copy.exitRecording}</button>
      </div>
    </section>
    <div className="rounded-lg border border-blue-500/30 bg-blue-500/10 px-4 py-3 text-sm text-blue-100">{copy.recordingModeNotice} {copy.maxDuration}: {formatDuration(status?.max_duration_s)} · {copy.storageAvailable}: {formatBytes(status?.storage.free_bytes)}</div>
    {error && <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-200">{error}</div>}
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">{CAMERAS.map((camera) => <CameraCard key={camera} camera={camera} status={status} language={language} busy={busy !== null} onStart={() => void run(`${camera}-start`, () => startRecording(camera))} onStop={() => void run(`${camera}-stop`, () => stopRecording(camera))} onExpand={() => setExpanded(camera)} />)}</div>
    <section className="card"><div className="mb-4 flex items-center gap-2"><Video aria-hidden="true" size={18} /><h2 className="font-semibold">{copy.recordingLibrary}</h2></div>
      {files.length === 0 ? <p className="text-sm text-muted">{copy.noRecordings}</p> : <div className="overflow-x-auto"><table className="recording-table"><thead><tr><th>Camera</th><th>Started</th><th>{copy.recordingElapsed}</th><th>{copy.recordingSize}</th><th aria-label="Actions" /></tr></thead><tbody>{files.map((file) => <tr key={file.id}><td>{cameraName(file.camera, language)}</td><td>{new Date(file.started_at * 1000).toLocaleString()}</td><td>{formatDuration(file.duration_s)}</td><td>{formatBytes(file.size_bytes)}</td><td><div className="flex justify-end gap-2"><button type="button" className="icon-button" onClick={() => setPlaying(file)} title={copy.playRecording} aria-label={copy.playRecording}><Play aria-hidden="true" size={18} fill="currentColor" /></button><a className="icon-button" href={getRecordingDownloadUrl(file.id)} title={copy.downloadRecording} aria-label={copy.downloadRecording}><Download aria-hidden="true" size={18} /></a></div></td></tr>)}</tbody></table></div>}
    </section>
    {expanded && <RecordingMonitor camera={expanded} language={language} onClose={() => setExpanded(null)} />}
    {playing && <div className="fixed inset-0 z-50 grid place-items-center bg-black/80 p-4" role="dialog" aria-modal="true" aria-label={copy.playRecording}><section className="w-full max-w-5xl rounded-lg border border-slate-700 bg-slate-900 p-4"><div className="mb-3 flex items-center justify-between"><h2 className="font-semibold">{cameraName(playing.camera, language)}</h2><button type="button" className="icon-button" onClick={() => setPlaying(null)} title={copy.closePlayer} aria-label={copy.closePlayer}><X aria-hidden="true" size={20} /></button></div><video className="aspect-video w-full bg-black" src={getRecordingPlayUrl(playing.id)} controls autoPlay /></section></div>}
  </div>
}
