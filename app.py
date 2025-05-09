from flask import Flask, render_template
from flask_socketio import SocketIO, emit
import threading
import serial
import time

app = Flask(__name__)
socketio = SocketIO(app)


ser = serial.Serial('/dev/ttyUSB0', 115200, timeout=1)

# 定义解析函数，提取64个点的数据
def parse_data_frame(data):
    """
    解析数据帧，返回64个点的值。
    """
    if len(data) < 516 or data[0:3] != b'\xAA\xAB\xAC':  
        return None

    points = []
    gamma = 0.5  # 非线性增强系数，值越小低值变化越明显

    for i in range(64):  # 提取64个点
        index = 3 + (i * 2)
        if index + 1 < len(data):
            high_byte = data[index]
            low_byte = data[index + 1]
            point_value = high_byte * 256 + low_byte

            # 处理非线性映射
            normalized_value = point_value / 65535  # 归一化到 0-1
            mapped_value = int(255 * (normalized_value ** gamma))  # 非线性映射
            points.append(mapped_value*10)
        else:
            points.append(0)  # 如果数据不足，填充0

    return points

# 串口监听线程
def serial_listener():
    """
    持续监听串口，解析数据并通过 Socket.IO 推送到前端。
    """
    while True:
        # 读取帧头
        frame_header = ser.read(3)
        if frame_header == b'\xAA\xAB\xAC':  # 检测帧头
            data = frame_header + ser.read(513)  # 读取完整帧
            if len(data) == 516:  # 验证数据帧长度
                points = parse_data_frame(data)
                if points:  # 如果解析成功
                    # 推送数据到前端
                    socketio.emit('update_colors', {'values': points})
                    print(f"Updated U-shape grid with values: {points}")
        time.sleep(0.1)  # 延迟，避免占用过高的CPU

@app.route('/')
def index():
    """
    渲染前端 HTML 页面。
    """
    return render_template('index.html')  # 确保index.html在templates文件夹中


if __name__ == '__main__':
    threading.Thread(target=serial_listener, daemon=True).start()  # 启动串口监听线程
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)

