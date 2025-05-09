# 首先导入eventlet并执行monkey_patch
import eventlet
eventlet.monkey_patch()

# 然后导入其他模块
from flask import Flask, render_template
from flask_socketio import SocketIO, emit
import serial
import time

app = Flask(__name__)
# 优化SocketIO配置
socketio = SocketIO(app, async_mode='eventlet', cors_allowed_origins="*", ping_timeout=10, ping_interval=5)

# 串口配置可能需要根据实际情况调整
ser = serial.Serial('/dev/ttyUSB0', 115200, timeout=0.5)  # 减少超时时间

# 数据缓存，减少重复发送相同数据
last_sent_data = None
# 控制发送频率计数器
send_counter = 0

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
            normalized_value = point_value / 65535  
            mapped_value = int(255 * (normalized_value ** gamma))  
            points.append(mapped_value*10)
        else:
            points.append(0)  

    return points


def serial_listener():
    """
    持续监听串口，解析数据并通过 Socket.IO 推送到前端。
    """
    global last_sent_data, send_counter
    buffer = bytearray()
    
    while True:
        try:
            # 读取可用的所有数据
            if ser.in_waiting > 0:
                new_data = ser.read(ser.in_waiting)
                buffer.extend(new_data)
                
                # 寻找帧头
                header_pos = buffer.find(b'\xAA\xAB\xAC')
                if header_pos >= 0 and len(buffer) >= header_pos + 516:
                    # 提取完整数据帧
                    frame = buffer[header_pos:header_pos + 516]
                    # 清理缓冲区，保留未处理部分
                    buffer = buffer[header_pos + 516:]
                    
                    # 解析数据
                    points = parse_data_frame(frame)
                    if points:
                        # 数据有变化或者每5个帧发送一次以保证频率
                        if points != last_sent_data or send_counter >= 5:
                            socketio.emit('update_colors', {'values': points})
                            last_sent_data = points
                            send_counter = 0
                        else:
                            send_counter += 1
            
            # 使用极短的延迟，让出CPU时间但不影响响应速度
            eventlet.sleep(0.01)  # 减少到10ms延迟
        except Exception as e:
            print(f"错误: {e}")
            eventlet.sleep(0.1)

@app.route('/')
def index():
    """
    渲染前端 HTML 页面。
    """
    return render_template('index.html')  # 确保index.html在templates文件夹中


if __name__ == '__main__':
    # 使用eventlet启动串口监听线程
    eventlet.spawn(serial_listener)
    # 运行SocketIO服务器
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)

