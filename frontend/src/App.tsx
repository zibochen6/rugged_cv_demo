import { useEffect, useState } from 'react'
import { Languages, Radio, RefreshCw, Video } from 'lucide-react'
import HubPage from './pages/HubPage'
import RecordingPage from './pages/RecordingPage'
import { enterRecordingMode, exitRecordingMode } from './api/recording'
import { COPY, errorText, type Language } from './i18n'
import './index.css'

export default function App() {
  const [language, setLanguage] = useState<Language>(() => {
    const saved = window.localStorage.getItem('visual-hub-language')
    return saved === 'zh' ? 'zh' : 'en'
  })
  const [view, setView] = useState<'live' | 'recording'>('live')
  const [modeBusy, setModeBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const copy = COPY[language]

  useEffect(() => {
    window.localStorage.setItem('visual-hub-language', language)
    document.documentElement.lang = language === 'zh' ? 'zh-CN' : 'en'
    document.title = copy.appTitle
  }, [language, copy.appTitle])

  const openRecording = async () => {
    if (view === 'recording' || modeBusy) return
    setModeBusy(true)
    setError(null)
    try {
      await enterRecordingMode()
      setView('recording')
    } catch (reason) {
      setError(`${copy.recordingModeFailed} ${errorText(language, reason)}`)
    } finally {
      setModeBusy(false)
    }
  }

  const openLive = async () => {
    if (view === 'live' || modeBusy) return
    setModeBusy(true)
    setError(null)
    try {
      await exitRecordingMode()
      setView('live')
    } catch (reason) {
      setError(`${copy.recordingCannotExit} ${errorText(language, reason)}`)
    } finally {
      setModeBusy(false)
    }
  }

  return (
    <div className="min-h-screen bg-bg text-text">
      <header className="sticky top-0 z-30 border-b border-slate-700/80 bg-slate-950/90 px-5 py-3 backdrop-blur">
        <div className="mx-auto flex max-w-[1800px] items-center justify-between">
          <div>
            <h1 className="text-lg font-bold tracking-wide">{copy.appTitle}</h1>
            <p className="mt-0.5 text-xs text-muted">Rugged J401 · Orin NX 16GB · JetPack 5.1.3</p>
          </div>
          <div className="flex items-center gap-2">
            <div className="segmented-control" role="tablist" aria-label="Visual Hub mode">
              <button type="button" role="tab" aria-selected={view === 'live'} className={view === 'live' ? 'segmented-control__item segmented-control__item--active' : 'segmented-control__item'} disabled={modeBusy} onClick={() => void openLive()}><Radio aria-hidden="true" size={16} />{copy.liveInference}</button>
              <button type="button" role="tab" aria-selected={view === 'recording'} className={view === 'recording' ? 'segmented-control__item segmented-control__item--active' : 'segmented-control__item'} disabled={modeBusy} onClick={() => void openRecording()}><Video aria-hidden="true" size={16} />{copy.recordingCenter}</button>
            </div>
            <div className="hidden rounded-full border border-emerald-500/30 bg-emerald-500/10 px-3 py-1 text-xs text-emerald-300 sm:block">
              {copy.soleEntry}
            </div>
            <button
              type="button"
              className="icon-button"
              onClick={() => window.location.reload()}
              aria-label={copy.refreshPage}
              title={copy.refreshPage}
            >
              <RefreshCw aria-hidden="true" size={19} strokeWidth={2} />
            </button>
            <button
              type="button"
              className="btn-secondary inline-flex items-center gap-2 text-sm"
              onClick={() => setLanguage((current) => current === 'en' ? 'zh' : 'en')}
              aria-label={copy.switchLanguageLabel}
              title={copy.switchLanguageLabel}
            >
              <Languages aria-hidden="true" size={17} />
              {copy.switchLanguage}
            </button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-[1800px] p-4 md:p-6">
        {error && <div className="mb-4 rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-200">{error}</div>}
        {view === 'live' ? <HubPage language={language} /> : <RecordingPage language={language} onExit={openLive} />}
      </main>
    </div>
  )
}
