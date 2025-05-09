from flask import Flask, render_template
from flask_socketio import SocketIO
import serial
import logging

# ── 基础配置 ─────────────────────────────────────────────
app = Flask(__name__)
socketio = SocketIO(app, async_mode="threading")   # 线程模式足够

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(threadName)s %(levelname)s: %(message)s"
)

# ── 串口 ────────────────────────────────────────────────
# 根据实际情况修改端口和波特率
ser = serial.Serial("/dev/ttyUSB0", 115200, timeout=1)

# ── 数据解析 ────────────────────────────────────────────
def parse_data_frame(data: bytes):
    """
    输入 516-byte 帧 → 返回 64 个 0-255 亮度值列表
    """
    if len(data) < 516 or data[:3] != b"\xAA\xAB\xAC":
        return None

    gamma = 0.5
    points = []

    for i in range(64):
        idx = 3 + i * 2
        high, low = data[idx], data[idx + 1]
        raw = high * 256 + low          # 0-65535
        norm = raw / 65535              # 0-1
        val = int(255 * (norm ** gamma))
        points.append(val)              # 不再 *10，避免溢出

    return points

# ── 后台线程 ────────────────────────────────────────────
def serial_listener():
    """持续监听串口 → 解析 → 推送到前端"""
    while True:
        header = ser.read(3)
        if header == b"\xAA\xAB\xAC":
            frame = header + ser.read(513)
            if len(frame) == 516:
                pts = parse_data_frame(frame)
                if pts:
                    socketio.emit("update_colors", {"values": pts})
                    logging.info("Frame emitted")
        socketio.sleep(0.03)            # 非阻塞睡眠 ≈30 ms

# ── Socket.IO 事件 ──────────────────────────────────────
@socketio.on("connect")
def on_connect():
    """有浏览器连上时，仅启动一次后台线程"""
    if not getattr(app, "serial_task_started", False):
        socketio.start_background_task(serial_listener)
        app.serial_task_started = True
        logging.info("Serial listener started")

# ── 路由 ────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")

# ── Main ───────────────────────────────────────────────
if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000,
                 debug=True, use_reloader=False)   # 禁用 reloader
