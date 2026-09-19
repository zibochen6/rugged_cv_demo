"""DMS (driver monitoring shell) demo — 演示级疲劳 / 头盔佩戴检测。

诚实口径（冻结，详见 docs/dms_helmet_demo.md 与 configs/dms.yaml 的 notices）:
  疲劳：MediaPipe Face Landmarker + 固定时间规则；不是经过个体标定的疲劳模型，
        未做任何 PERCLOS 标定；无近红外相机时夜间/强逆光不可用。
  头盔：人体框 → 头部 ROI 的颜色/肤色/暗区启发式，按人输出
        佩戴 / 未佩戴 / 未知（三值），不输出任何百分比式结论。
  两者都只是演示，不构成安全认证，不得用于合规判定。

模块清单:
  config   - configs/dms.yaml 的加载（默认值兜底，缺文件也不崩）
  camera   - 相机源 usb:/rtsp://video:/image:/synthetic + 明确的不可用错误
  state    - DmsRuntime：两个开关的唯一真源 + /state 快照 + 推理计数
  face     - Haar 级联路径解析（cv2 无 cv2.data）+ 主脸检测/跟踪
  fatigue  - 疲劳信号与去抖状态机（DISABLED/UNKNOWN/NORMAL/DROWSY_*）
  helmet   - 头盔三值判定 + 按 track_id 多数票平滑
  events   - logs/dms_events.jsonl 事件日志（一行一 JSON）
  render   - 画面叠加与画面内复选框（纯 ASCII，cv2.putText 不能渲染中文）
  web      - 零额外依赖 MJPEG 服务（自行实现，不 import app/web_stream.py）

入口: app/dms_app.py（--mode web|display）。一键脚本: scripts/run_dms_demo.sh。
"""

__all__ = [
    "camera",
    "config",
    "events",
    "face",
    "fatigue",
    "helmet",
    "render",
    "state",
    "web",
]
