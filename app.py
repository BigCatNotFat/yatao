# 首先导入eventlet并执行monkey_patch
import eventlet
eventlet.monkey_patch()

# 然后导入其他模块
from flask import Flask, render_template
from flask_socketio import SocketIO, emit
import serial
import time
import os

app = Flask(__name__)
# 优化SocketIO配置
socketio = SocketIO(app, async_mode='eventlet', cors_allowed_origins="*", ping_timeout=10, ping_interval=5)

# 串口配置
serial_connected = False
ser = None
last_reconnect_attempt = 0

# 数据发送控制
last_sent_data = None
send_counter = 0
last_error_time = 0

def connect_serial():
    """尝试连接串口设备，支持自动重连"""
    global ser, serial_connected
    
    try:
        # 对于Windows系统使用COM端口，对于Linux使用/dev
        if os.name == 'nt':
            port = 'COM3'  # 请根据实际情况调整Windows端口
        else:
            port = '/dev/ttyUSB0'  # 请根据实际情况调整Linux端口
            
        # 关闭已有连接
        if ser is not None:
            try:
                ser.close()
            except:
                pass
                
        # 建立新连接
        ser = serial.Serial(port, 115200, timeout=0.5)
        serial_connected = True
        print(f"串口设备 {port} 连接成功")
        return True
    except Exception as e:
        serial_connected = False
        print(f"串口连接失败: {e}")
        return False

# 初始连接尝试
connect_serial()

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
    global last_sent_data, send_counter, serial_connected, ser, last_reconnect_attempt, last_error_time
    buffer = bytearray()
    error_count = 0
    
    while True:
        try:
            # 检查串口连接状态
            if not serial_connected:
                current_time = time.time()
                # 每5秒尝试一次重连
                if current_time - last_reconnect_attempt > 5:
                    last_reconnect_attempt = current_time
                    if connect_serial():
                        buffer = bytearray()  # 重置缓冲区
                        error_count = 0
                eventlet.sleep(0.5)
                continue
                
            # 读取可用数据
            if ser and ser.in_waiting > 0:
                try:
                    new_data = ser.read(ser.in_waiting)
                    buffer.extend(new_data)
                    error_count = 0  # 重置错误计数
                except Exception as e:
                    current_time = time.time()
                    # 限制错误日志频率，避免刷屏
                    if current_time - last_error_time > 5:
                        print(f"读取错误: {e}")
                        last_error_time = current_time
                    
                    error_count += 1
                    if error_count > 5:
                        # 多次错误后标记设备断开
                        serial_connected = False
                        print("串口设备可能已断开，将尝试重新连接")
                    eventlet.sleep(0.5)
                    continue
                
                # 寻找帧头
                header_pos = buffer.find(b'\xAA\xAB\xAC')
                if header_pos >= 0:
                    # 丢弃帧头之前的数据
                    if header_pos > 0:
                        buffer = buffer[header_pos:]
                        header_pos = 0
                    
                    # 如果有完整的帧
                    if len(buffer) >= 516:
                        # 提取完整数据帧
                        frame = buffer[:516]
                        # 更新缓冲区，移除已处理的帧
                        buffer = buffer[516:]
                        
                        # 解析数据
                        points = parse_data_frame(frame)
                        if points:
                            # 减少发送频率避免大波动：数据变化较大或计数器达到阈值时发送
                            should_send = False
                            
                            # 检查数据变化
                            if last_sent_data is None:
                                should_send = True
                            else:
                                # 计算数据变化量
                                change_count = sum(1 for old, new in zip(last_sent_data, points) if abs(old - new) > 5)
                                if change_count > 8:  # 如果超过8个点有较大变化则发送
                                    should_send = True
                            
                            # 根据计数器定期发送
                            if send_counter >= 3:  # 降低到每3个有效帧发送一次
                                should_send = True
                            
                            if should_send:
                                socketio.emit('update_colors', {'values': points})
                                last_sent_data = points.copy()  # 使用copy避免引用问题
                                send_counter = 0
                            else:
                                send_counter += 1
            
            # 使用短延迟避免CPU占用过高
            eventlet.sleep(0.01)
            
        except Exception as e:
            current_time = time.time()
            if current_time - last_error_time > 5:
                print(f"监听错误: {e}")
                last_error_time = current_time
            eventlet.sleep(0.1)

@app.route('/')
def index():
    """
    渲染前端 HTML 页面。
    """
    return render_template('index.html')


# 添加状态API端点
@app.route('/api/status')
def status():
    """返回串口连接状态"""
    return {"connected": serial_connected}


if __name__ == '__main__':
    # 使用eventlet启动串口监听线程
    eventlet.spawn(serial_listener)
    # 运行SocketIO服务器
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)

