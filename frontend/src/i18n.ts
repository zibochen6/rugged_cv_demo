import type { HubEvent, ModuleId } from './api/hub'

export type Language = 'en' | 'zh'

const EN = {
  appTitle: 'Forklift Vision Control',
  soleEntry: 'Unified console · 8000',
  switchLanguage: '中文',
  switchLanguageLabel: 'Switch interface to Chinese',
  refreshPage: 'Refresh display',
  liveInference: 'Live Inference',
  recordingCenter: 'Recording Center',
  recordingModeNotice: 'Recording mode is active. Inference is stopped until you return to Live Inference.',
  recordingTitle: 'Pure Camera Recording',
  recordingHelp: 'Records raw camera video only. No models, detection, overlays, or audio are used.',
  exitRecording: 'Return to Live Inference',
  startRecording: 'Start recording',
  stopRecording: 'Stop recording',
  startAllRecordings: 'Record all',
  stopAllRecordings: 'Stop all recordings',
  recordingIdle: 'Start recording to open the live preview.',
  recordingStarted: 'Started',
  actions: 'Actions',
  recordingActive: 'Recording',
  recordingPreview: 'Recording preview',
  recordingElapsed: 'Elapsed',
  recordingSize: 'File size',
  recordingFrames: 'Frames',
  maxDuration: 'Maximum duration',
  storageAvailable: 'Storage available',
  recordingLibrary: 'Recording library',
  noRecordings: 'No completed recordings yet.',
  playRecording: 'Play recording',
  downloadRecording: 'Download recording',
  closePlayer: 'Close player',
  rawFrontCamera: 'Front camera',
  rawRearCamera: 'Rear camera',
  rawCabinCamera: 'Cabin camera',
  recordingCannotExit: 'Stop all recordings before returning to Live Inference.',
  recordingModeFailed: 'Could not change recording mode.',
  situationTitle: 'Live Safety Overview',
  channelsRunning: 'channels running',
  safetyPriority: 'Rear safety has priority · Closing this page does not stop inference · Use Stop to release resources',
  startAll: 'Start All',
  stopAll: 'Stop All',
  waitingToStart: 'Waiting to start',
  connectingCamera: 'Connecting to camera...',
  liveView: 'live view',
  fullscreen: 'View fullscreen',
  linkHealthy: 'Link healthy',
  notReady: 'Not ready',
  capture: 'Capture',
  inference: 'Inference',
  frameAge: 'Frame age',
  showPerformance: 'Show FPS metrics',
  hidePerformance: 'Hide FPS metrics',
  start: 'Start',
  stop: 'Stop',
  restart: 'Restart',
  cameraSource: 'Camera',
  selectCamera: 'Choose camera',
  cameraLoadFailed: 'Could not list cameras.',
  camerasEmpty: 'No cameras detected. Enter a source manually.',
  cameraInUse: 'in use by',
  cameraUnreachable: 'not responding',
  cameraNotAllowed: 'not supported by this module',
  customSource: 'Enter a source manually',
  customSourcePlaceholder: 'rtsp://user:password@host:554/ or video:/path/clip.mp4',
  customSourceApply: 'Use this source',
  customSourceCancel: 'Cancel',
  customSourceFailed: 'Unsupported source, or it could not be read.',
  customSourceBusy: 'Checking...',
  switchRestarts: 'Switching the camera restarts this module. Continue?',
  cameraNone: 'Not configured',
  rearCalibrationWarning: 'Rear depth thresholds were calibrated for the previous camera — re-check them after switching.',
  frontGeometryNote: 'Front segmentation geometry and click mapping are not calibrated for this source.',
  thermalState: 'Thermal state',
  criticalDegradation: 'Critical degradation',
  constrainedOperation: 'Constrained operation',
  rearConfigTitle: 'Rear Safety Configuration',
  rearConfigHelp: 'The danger distance must be lower than the warning distance. Changes apply immediately after saving.',
  dangerDistance: 'Danger distance',
  warningDistance: 'Warning distance',
  buzzer: 'Buzzer',
  saveApply: 'Save and Apply',
  saving: 'Saving...',
  invalidThresholds: 'Danger distance must be lower than warning distance.',
  rearMustRun: 'Start Rear Warning before applying this configuration.',
  cabinDetection: 'Cabin Detection',
  cabinHelp: 'Uses the USB cabin camera by default; the camera can be changed per module in the card above.',
  fatigueDetection: 'Fatigue detection',
  fatigueAlarmBuzzer: 'Fatigue alarm buzzer',
  helmetDetection: 'Helmet detection',
  cabinNote: 'Disabling a detector stops its inference. Stopping Cabin Detection releases its camera.',
  eventStream: 'Unified Event Stream',
  noEvents: 'No events',
  backToHub: 'Back to control center',
  inferenceContinues: 'Inference remains active',
  escToReturn: 'Esc to return',
  clearTarget: 'Clear target',
  loadingSegmentation: 'Loading front segmentation model...',
  configFailed: 'Could not apply rear safety configuration. Previous values were restored.',
  operationFailed: 'Operation failed. Check the module log for details.',
} as const

type CopyKey = keyof typeof EN

const ZH: Record<CopyKey, string> = {
  appTitle: '叉车三路视觉总控',
  soleEntry: '统一入口 · 8000',
  switchLanguage: 'English',
  switchLanguageLabel: '将界面切换为英文',
  refreshPage: '刷新页面',
  liveInference: '实时推理',
  recordingCenter: '录制中心',
  recordingModeNotice: '录制模式已启用。返回实时推理前，全部推理将保持停止。',
  recordingTitle: '纯摄像头录制',
  recordingHelp: '仅保存原始摄像头画面，不运行模型、检测、叠加层或音频。',
  exitRecording: '返回实时推理',
  startRecording: '开始录制',
  stopRecording: '停止录制',
  startAllRecordings: '全部录制',
  stopAllRecordings: '停止全部录制',
  recordingIdle: '开始录制后开启实时预览。',
  recordingStarted: '开始时间',
  actions: '操作',
  recordingActive: '录制中',
  recordingPreview: '录制预览',
  recordingElapsed: '时长',
  recordingSize: '文件大小',
  recordingFrames: '帧数',
  maxDuration: '最长时长',
  storageAvailable: '可用存储空间',
  recordingLibrary: '录像库',
  noRecordings: '暂无已完成的录像。',
  playRecording: '播放录像',
  downloadRecording: '下载录像',
  closePlayer: '关闭播放器',
  rawFrontCamera: '前摄',
  rawRearCamera: '后摄',
  rawCabinCamera: '座舱摄像头',
  recordingCannotExit: '请停止全部录制后再返回实时推理。',
  recordingModeFailed: '无法切换录制模式。',
  situationTitle: '实时安全态势',
  channelsRunning: '路运行',
  safetyPriority: '后视安全优先 · 页面关闭不会停止推理 · 请使用显式停止按钮释放资源',
  startAll: '一键全开',
  stopAll: '停止全部',
  waitingToStart: '等待启动',
  connectingCamera: '正在连接摄像头...',
  liveView: '实时画面',
  fullscreen: '全屏监视',
  linkHealthy: '链路正常',
  notReady: '未就绪',
  capture: '捕获',
  inference: '推理',
  frameAge: '帧龄',
  showPerformance: '显示 FPS 指标',
  hidePerformance: '隐藏 FPS 指标',
  start: '启动',
  stop: '停止',
  restart: '重启',
  cameraSource: '摄像头',
  selectCamera: '选择摄像头',
  cameraLoadFailed: '无法获取摄像头列表。',
  camerasEmpty: '未检测到摄像头，请手动填写源。',
  cameraInUse: '已被占用：',
  cameraUnreachable: '无响应',
  cameraNotAllowed: '该模块不支持',
  customSource: '手动填写源',
  customSourcePlaceholder: 'rtsp://user:password@host:554/ 或 video:/path/clip.mp4',
  customSourceApply: '使用该源',
  customSourceCancel: '取消',
  customSourceFailed: '源不支持，或无法打开。',
  customSourceBusy: '检测中...',
  switchRestarts: '切换摄像头会重启该模块，是否继续？',
  cameraNone: '未配置',
  rearCalibrationWarning: '后视距离阈值是按原摄像头标定的，切换后请重新核对。',
  frontGeometryNote: '前视分割的几何与点击映射未针对该源标定。',
  thermalState: '温控状态',
  criticalDegradation: '临界降级',
  constrainedOperation: '受限运行',
  rearConfigTitle: '后视安全配置',
  rearConfigHelp: '危险距离必须小于警告距离，保存后立即生效。',
  dangerDistance: '危险距离',
  warningDistance: '警告距离',
  buzzer: '蜂鸣器',
  saveApply: '保存并应用',
  saving: '正在保存...',
  invalidThresholds: '危险距离必须小于警告距离。',
  rearMustRun: '请先启动后视预警再应用配置。',
  cabinDetection: '座舱检测',
  cabinHelp: '默认使用 USB 座舱摄像头；摄像头可在上方各模块卡片中单独选择。',
  fatigueDetection: '疲劳检测',
  fatigueAlarmBuzzer: '疲劳报警蜂鸣器',
  helmetDetection: '头盔检测',
  cabinNote: '关闭某项会停止该项推理；停止座舱会释放其摄像头。',
  eventStream: '统一事件流',
  noEvents: '暂无事件',
  backToHub: '返回三路总控',
  inferenceContinues: '推理持续运行',
  escToReturn: 'Esc 返回',
  clearTarget: '清除目标',
  loadingSegmentation: '正在加载前视分割模型...',
  configFailed: '后视安全配置应用失败，已恢复之前的值。',
  operationFailed: '操作失败，请检查模块日志。',
}

export const COPY: Record<Language, Record<CopyKey, string>> = { en: EN, zh: ZH }

const MODULE_COPY: Record<Language, Record<ModuleId, { label: string; camera: string }>> = {
  en: {
    front: { label: 'Front Segmentation', camera: 'PoE Front Camera' },
    rear: { label: 'Rear Warning', camera: 'PoE Rear Camera' },
    dms: { label: 'Cabin Detection', camera: 'USB Cabin Camera' },
  },
  zh: {
    front: { label: '前视分割', camera: 'PoE 前摄' },
    rear: { label: '后视预警', camera: 'PoE 后摄' },
    dms: { label: '座舱检测', camera: 'USB 座舱摄像头' },
  },
}

const STATE_COPY: Record<Language, Record<string, string>> = {
  en: { running: 'Running', starting: 'Starting', degraded: 'Degraded', error: 'Error', stopping: 'Stopping', stopped: 'Stopped' },
  zh: { running: '运行中', starting: '启动中', degraded: '降级', error: '故障', stopping: '停止中', stopped: '已停止' },
}

export function moduleCopy(language: Language, id: ModuleId) {
  return MODULE_COPY[language][id]
}

/**
 * Localized module name for an id that arrives as a plain string (for example
 * `camera.in_use_by`). Falls back to the raw id, never to the other language.
 */
export function moduleLabel(language: Language, id: string) {
  return MODULE_COPY[language][id as ModuleId]?.label || id.toUpperCase()
}

export function stateText(language: Language, state?: string) {
  return STATE_COPY[language][state || ''] || STATE_COPY[language].stopped
}

export function eventText(language: Language, event: HubEvent) {
  if (language === 'zh') return event.message
  const moduleName = event.source === 'hub'
    ? 'Visual Hub'
    : MODULE_COPY.en[event.source as ModuleId]?.label || event.source.toUpperCase()
  const payload = event.payload || {}
  if (event.kind === 'risk') {
    const level = String(payload.level || 'status')
    const distance = payload.distance == null ? '' : ` · Distance ${payload.distance} m`
    const ttc = payload.ttc == null ? '' : ` · TTC ${payload.ttc} s`
    return `${moduleName}: ${level}${distance}${ttc}`
  }
  if (event.kind === 'fatigue_state') {
    return `${moduleName}: Fatigue status ${String(payload.state || 'UNKNOWN')}`
  }
  if (event.kind === 'helmet_verdict') {
    const verdict = String(payload.verdict || 'unknown').replace(/_/g, ' ')
    const people = Array.isArray(payload.persons) ? payload.persons.length : 0
    return `${moduleName}: Helmet ${verdict} (${people} people)`
  }
  if (event.kind === 'session') {
    if (/stopped|已停止/.test(event.message)) return `${moduleName} stopped and resources released`
    if (/ready|就绪/.test(event.message)) return `${moduleName} ready`
    if (/started|已启动/.test(event.message)) return `${moduleName} started`
    const switched = event.message.match(/切换为\s*(.+)$/)
    if (switched) return `${moduleName} camera switched to ${switched[1]}`
  }
  if (!/[\u3400-\u9fff]/.test(event.message)) return event.message
  return `${moduleName} reported a ${event.kind.replace(/_/g, ' ')} event`
}

export function errorText(language: Language, value: unknown) {
  const raw = value instanceof Error ? value.message : String(value)
  if (language === 'zh' || !/[\u3400-\u9fff]/.test(raw)) return raw
  if (raw.includes('危险距离')) return EN.invalidThresholds
  if (raw.includes('相机') && raw.includes('占用')) return 'The camera is already in use by another module.'
  if (raw.includes('启动失败')) return 'The module failed to start. Check the module log for details.'
  if (raw.includes('异常退出')) return 'The module exited unexpectedly. Check the module log for details.'
  if (raw.includes('未配置')) return 'The camera source is not configured.'
  if (raw.includes('不支持的摄像头源')) return 'Unsupported camera source.'
  if (raw.includes('不支持该源类型')) return 'This module does not support that source type.'
  if (raw.includes('正在运行')) return 'Stop the module before switching its camera.'
  if (raw.includes('无法持久化')) return 'Could not save the camera choice. Check the hub log.'
  if (raw.includes('未知的摄像头')) return 'That camera is no longer detected. Reload the list.'
  return EN.operationFailed
}
