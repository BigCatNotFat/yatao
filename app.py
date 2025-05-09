# 首先导入eventlet并执行monkey_patch
import eventlet
eventlet.monkey_patch()

# 然后导入其他模块
from flask import Flask, render_template
from flask_socketio import SocketIO, emit
import threading
import serial
import time
import os
import fcntl
import errno

app = Flask(__name__)
# 优化SocketIO配置
socketio = SocketIO(app, async_mode='eventlet', cors_allowed_origins="*", ping_timeout=10, ping_interval=5)

# 串口配置
ser = None
serial_connected = False
last_reconnect_time = 0

# 控制数据发送频率的变量
last_data_send_time = 0
data_send_interval = 2.0  # 2秒发送一次，大幅降低频率

# 数据变化监控
last_sent_data = None
min_change_threshold = 20  # 至少有20%的数据点变化才发送新数据

def connect_serial():
    """连接串口设备"""
    global ser, serial_connected
    
    try:
        # 关闭已有连接
        if ser is not None:
            try:
                ser.close()
            except:
                pass
        
        # 选择正确的串口设备
        if os.name == 'nt':  # Windows
            port = 'COM3'  # 请根据实际情况调整
        else:  # Linux/Mac
            port = '/dev/ttyUSB0'
            
        # 建立连接
        ser = serial.Serial(port, 115200, timeout=1)
        serial_connected = True
        print(f"串口设备 {port} 连接成功")
        return True
    except Exception as e:
        serial_connected = False
        print(f"串口连接失败: {e}")
        return False

# 初始连接
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

def data_has_significant_change(old_data, new_data):
    """检查数据是否有显著变化"""
    if not old_data or not new_data:
        return True
        
    # 计算变化显著的点数
    changes = sum(1 for old, new in zip(old_data, new_data) if abs(old - new) > 10)
    
    # 计算变化百分比
    change_percent = (changes / len(old_data)) * 100
    
    # 如果变化百分比超过阈值，认为有显著变化
    return change_percent >= min_change_threshold

def safe_read(length):
    """安全读取串口数据，处理可能的异常"""
    global ser, serial_connected
    
    if not ser or not serial_connected:
        return b''
        
    try:
        # 首先检查是否有数据可读
        waiting = ser.in_waiting
        if waiting <= 0:
            return b''
            
        # 限制读取长度，避免长时间阻塞
        actual_length = min(waiting, length)
        if actual_length <= 0:
            return b''
            
        # 读取数据
        data = ser.read(actual_length)
        return data
    except (serial.SerialException, OSError, IOError) as e:
        if hasattr(e, 'errno') and e.errno == errno.EAGAIN:
            # 资源暂时不可用，非致命错误
            return b''
            
        # 其他错误视为连接问题
        print(f"串口读取错误: {e}")
        serial_connected = False
        return b''

def serial_listener():
    """
    持续监听串口，解析数据并通过 Socket.IO 推送到前端。
    """
    global ser, serial_connected, last_reconnect_time, last_data_send_time, last_sent_data
    
    # 最后一次成功解析的数据帧
    current_points = None
    read_error_count = 0
    print_log_count = 0  # 用于限制日志打印频率
    
    while True:
        # 检查串口连接状态，如果断开则尝试重连
        if not serial_connected:
            current_time = time.time()
            if current_time - last_reconnect_time > 5:  # 每5秒尝试一次
                last_reconnect_time = current_time
                connect_serial()
            time.sleep(1.0)  # 增加重连等待时间
            continue
            
        try:
            # 读取帧头 - 保持原始简单逻辑
            frame_header = ser.read(3)
            if frame_header == b'\xAA\xAB\xAC':  # 检测帧头
                data = frame_header + ser.read(513)  # 读取完整帧
                if len(data) == 516:  # 验证数据帧长度
                    points = parse_data_frame(data)
                    if points:  # 如果解析成功
                        # 更新最新数据
                        current_points = points
                        read_error_count = 0  # 重置错误计数
                        
                        # 检查是否应该发送数据
                        current_time = time.time()
                        should_send = False
                        
                        # 时间间隔检查
                        time_ok = current_time - last_data_send_time >= data_send_interval
                        
                        # 数据变化检查
                        change_ok = data_has_significant_change(last_sent_data, current_points)
                        
                        # 满足条件则发送
                        if time_ok and (change_ok or not last_sent_data):
                            # 推送数据到前端
                            socketio.emit('update_colors', {'values': current_points})
                            
                            # 限制打印频率，每10次发送才打印一次
                            print_log_count += 1
                            if print_log_count >= 10:
                                print(f"已更新数据，变化率: {(sum(1 for old, new in zip(last_sent_data or current_points, current_points) if abs(old - new) > 10) / len(current_points) * 100):.1f}%")
                                print_log_count = 0
                                
                            # 更新发送时间和数据
                            last_data_send_time = current_time
                            last_sent_data = current_points.copy()
            
            # 延长循环延迟到200ms，减少CPU占用
            time.sleep(0.2)
        except Exception as e:
            read_error_count += 1
            # 只在连续多次错误后才打印
            if read_error_count >= 5:
                print(f"串口读取错误: {e}")
                read_error_count = 0
                serial_connected = False  # 标记连接断开
            time.sleep(0.5)

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


# 添加重置端点
@app.route('/api/reset')
def reset_serial():
    """手动重置串口连接"""
    global serial_connected
    serial_connected = False
    return {"status": "重置中", "success": True}


# 添加频率调整端点
@app.route('/api/set_frequency/<float:interval>')
def set_frequency(interval):
    """设置数据发送频率（秒）"""
    global data_send_interval
    
    # 扩大范围到10秒
    if 0.5 <= interval <= 10.0:
        data_send_interval = interval
        return {"status": "成功", "interval": interval}
    else:
        return {"status": "失败", "message": "频率必须在0.5到10秒之间"}, 400


if __name__ == '__main__':
    # 启动串口监听线程
    threading.Thread(target=serial_listener, daemon=True).start()
    # 运行SocketIO服务器
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)

