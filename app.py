# 首先导入eventlet并执行monkey_patch
import eventlet
eventlet.monkey_patch()

# 然后导入其他模块
from flask import Flask, render_template
from flask_socketio import SocketIO, emit
import serial
import time
import os
import fcntl
import errno

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
        # 关闭已有连接
        if ser is not None:
            try:
                ser.close()
                time.sleep(0.5)  # 等待端口释放
            except:
                pass
        
        # 对于Windows系统使用COM端口，对于Linux使用/dev
        if os.name == 'nt':
            port = 'COM3'  # 请根据实际情况调整Windows端口
        else:
            port = '/dev/ttyUSB0'  # 请根据实际情况调整Linux端口
            
        # 检查设备是否存在
        if os.name != 'nt' and not os.path.exists(port):
            print(f"错误: 设备 {port} 不存在")
            return False
            
        # 建立新连接 - 禁用硬件流控制，增加更长的超时
        ser = serial.Serial(
            port=port,
            baudrate=115200,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            xonxoff=False,     # 禁用软件流控
            rtscts=False,      # 禁用硬件RTS/CTS流控
            dsrdtr=False,      # 禁用硬件DSR/DTR流控
            timeout=1,         # 读取超时
            write_timeout=1    # 写入超时
        )
        
        # 在Linux上尝试获取独占访问权
        if os.name != 'nt':
            try:
                # 避免在Windows上导入错误
                fcntl.flock(ser.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (ImportError, IOError, AttributeError) as e:
                print(f"无法获取串口独占访问: {e}")
        
        # 清空缓冲区
        ser.reset_input_buffer()
        ser.reset_output_buffer()
        
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
    global last_sent_data, send_counter, serial_connected, ser, last_reconnect_attempt, last_error_time
    buffer = bytearray()
    error_count = 0
    read_interval = 0.05  # 20Hz读取频率，降低访问频率
    last_read_time = 0
    
    while True:
        try:
            current_time = time.time()
            
            # 检查串口连接状态
            if not serial_connected:
                # 每5秒尝试一次重连
                if current_time - last_reconnect_attempt > 5:
                    last_reconnect_attempt = current_time
                    if connect_serial():
                        buffer = bytearray()  # 重置缓冲区
                        error_count = 0
                eventlet.sleep(0.5)
                continue
            
            # 控制读取频率
            if current_time - last_read_time < read_interval:
                eventlet.sleep(0.01)
                continue
                
            last_read_time = current_time
                
            # 安全读取数据
            new_data = safe_read(512)  # 一次最多读取512字节
            
            if new_data:
                buffer.extend(new_data)
                error_count = 0  # 重置错误计数
            else:
                # 如果连续多次没有读到数据，增加错误计数
                error_count += 1
                if error_count > 100:  # 更高的容错阈值
                    error_count = 0
                    if ser and serial_connected:
                        print("长时间无数据，尝试重置串口")
                        try:
                            ser.reset_input_buffer()
                        except Exception as e:
                            print(f"重置缓冲区失败: {e}")
                            serial_connected = False
                
                eventlet.sleep(0.05)
                continue
            
            # 寻找帧头
            while True:
                header_pos = buffer.find(b'\xAA\xAB\xAC')
                if header_pos < 0 or len(buffer) < header_pos + 516:
                    break  # 没有找到完整帧，等待更多数据
                    
                # 帧同步，丢弃帧头前的数据
                if header_pos > 0:
                    buffer = buffer[header_pos:]
                    header_pos = 0
                    continue  # 重新检查
                
                # 提取完整数据帧
                if len(buffer) >= 516:
                    frame = buffer[:516]
                    buffer = buffer[516:]  # 更新缓冲区
                    
                    # 解析数据
                    points = parse_data_frame(frame)
                    if points:
                        # 减少发送频率避免大波动
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
                        if send_counter >= 3:  # 每3个有效帧发送一次
                            should_send = True
                        
                        if should_send:
                            try:
                                socketio.emit('update_colors', {'values': points})
                                last_sent_data = points.copy()  # 使用copy避免引用问题
                                send_counter = 0
                            except Exception as e:
                                print(f"数据发送错误: {e}")
                        else:
                            send_counter += 1
                else:
                    break  # 等待更多数据
            
            # 控制循环延迟
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


# 添加重置串口端点
@app.route('/api/reset')
def reset_serial():
    """重置串口连接"""
    global serial_connected
    serial_connected = False
    return {"status": "重置中", "success": True}


if __name__ == '__main__':
    # 使用eventlet启动串口监听线程
    eventlet.spawn(serial_listener)
    # 运行SocketIO服务器
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)

