from flask import Flask, render_template
from flask_socketio import SocketIO
import serial
import logging

# ---------- 基础配置 ----------
app = Flask(__name__)
socketio = SocketIO(app, async_mode="threading")  # 纯线程模式即可

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(threadName)s %(levelname)s: %(message)s"
)

# ---------- 串口 ----------
# 请按需修改串口号 / 波特率
ser = serial.Serial("/dev/ttyUSB0", 115200, timeout=1)


# ---------- 数据解析 ----------
def parse_data_frame(data: bytes):
    """
    输入 516 byte 帧 → 返回 64 个 0-255 亮度值列表
    """
    if len(data) < 516 or data[:3] != b"\xAA\xAB\xAC":
        return None

    gamma = 0.5          # 非线性增强系数
    points = []

    for i in range(64):
        idx = 3 + i * 2
        high, low = data[idx], data[idx + 1]
        raw = high * 256 + low            # 0-65535
        norm = raw / 65535                # 归一化 0-1
        mapped = int(255 * (norm ** gamma))
        points.append(mapped)             # 不再 *10，避免 >255

    return points


# ---------- 后台线程 ----------
def serial_listener():
    """
    持续监听串口 → 解析 → 推送到前端
    （使用 socketio.sleep() 防阻塞）
    """
    while True:
        header = ser.read(3)
        if header == b"\xAA\xAB\xAC":
            frame = header + ser.read(513)
            if len(frame) == 516:
                pts = parse_data_frame(frame)
                if pts:
                    socketio.emit("update_colors", {"values": pts})
                    logging.info("Frame emitted to browser")
        socketio.sleep(0.03)     # 非阻塞睡眠 ≈30 ms


# ---------- Socket.IO 事件 ----------
@socketio.on("connect")
def handle_connect():
    """
    每当有浏览器连接，如果后台线程没开过就启动一次
    """
    if not getattr(app, "serial_task_started", False):
        socketio.start_background_task(serial_listener)
        app.serial_task_started = True
        logging.info("Serial listener started")


# ---------- 路由 ----------
@app.route("/")
def index():
    return render_template("index.html")


# ---------- Main ----------
if __name__ == "__main__":
    # 禁用 reloader，防止双进程
    socketio.run(app, host="0.0.0.0", port=5000, debug=True, use_reloader=False)
