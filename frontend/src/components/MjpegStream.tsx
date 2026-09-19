import { useCallback, useEffect, useRef, useState } from 'react'

const RETRY_DELAY_MS = 900
const FIRST_FRAME_TIMEOUT_MS = 3500
const VISIBILITY_FALLBACK_MS = 1200

function cacheBustedUrl(url: string, attempt: number) {
  const separator = url.includes('?') ? '&' : '?'
  return `${url}${separator}stream_attempt=${attempt}`
}

interface MjpegStreamProps {
  src: string
  alt: string
  className?: string
  imageClassName?: string
  loadingLabel: string
}

/**
 * MJPEG endpoints can legitimately return 503 or close while a module is
 * starting. Browsers do not retry a failed <img> request, so reconnect here
 * instead of requiring an operator to refresh a touchscreen display.
 */
export default function MjpegStream({
  src,
  alt,
  className = '',
  imageClassName = '',
  loadingLabel,
}: MjpegStreamProps) {
  const [attempt, setAttempt] = useState(0)
  const [ready, setReady] = useState(false)
  const retryTimer = useRef<number | null>(null)

  const clearRetry = useCallback(() => {
    if (retryTimer.current !== null) {
      window.clearTimeout(retryTimer.current)
      retryTimer.current = null
    }
  }, [])

  const retry = useCallback(() => {
    clearRetry()
    retryTimer.current = window.setTimeout(() => {
      retryTimer.current = null
      setReady(false)
      setAttempt((value) => value + 1)
    }, RETRY_DELAY_MS)
  }, [clearRetry])

  useEffect(() => {
    clearRetry()
    setReady(false)
    setAttempt(0)
  }, [src, clearRetry])

  useEffect(() => {
    if (ready) return undefined
    const timeout = window.setTimeout(() => {
      setAttempt((value) => value + 1)
    }, FIRST_FRAME_TIMEOUT_MS)
    return () => window.clearTimeout(timeout)
  }, [attempt, ready])

  useEffect(() => {
    if (ready) return undefined
    // Firefox 120 on JetPack can render multipart MJPEG frames without firing
    // img.onload until the connection closes. Do not keep a valid live stream
    // transparent behind the loading panel on the local kiosk display.
    const timeout = window.setTimeout(() => setReady(true), VISIBILITY_FALLBACK_MS)
    return () => window.clearTimeout(timeout)
  }, [attempt, ready])

  useEffect(() => () => clearRetry(), [clearRetry])

  const markReady = useCallback(() => {
    clearRetry()
    setReady(true)
  }, [clearRetry])

  const markUnavailable = useCallback(() => {
    setReady(false)
    retry()
  }, [retry])

  return (
    <div className={`mjpeg-stream relative overflow-hidden bg-black ${className}`}>
      <img
        key={attempt}
        src={cacheBustedUrl(src, attempt)}
        alt={alt}
        className={`${imageClassName} ${ready ? 'opacity-100' : 'opacity-0'}`}
        onLoad={markReady}
        onError={markUnavailable}
        draggable={false}
      />
      {!ready && (
        <div className="absolute inset-0 grid place-items-center bg-black text-center text-sm text-slate-300" aria-live="polite">
          {loadingLabel}
        </div>
      )}
    </div>
  )
}
