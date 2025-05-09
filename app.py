from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO, emit
import threading
import serial
import time
import os

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

# 最新数据缓存
latest_data = None

# 串口设置和状态跟踪
ser = None
serial_connected = False
last_reconnect_time = 0

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
            normalized_value = point_value / 65535  # 归一化到 0-1
            mapped_value = int(255 * (normalized_value ** gamma))  # 非线性映射
            points.append(mapped_value*10)
        else:
            points.append(0)  # 如果数据不足，填充0

    return points

# 添加Socket.IO事件处理器
@socketio.on('connect')
def handle_connect():
    """处理客户端连接"""
    print(f"客户端连接: {request.sid}")
    # 如果有最新数据，立即发送给新连接的客户端
    if latest_data:
        emit('update_colors', {'values': latest_data})

@socketio.on('test_connection')
def handle_test(data):
    """处理测试连接消息"""
    print(f"收到测试连接消息: {data}")
    emit('message', {'status': 'connected', 'message': '连接测试成功'})

@socketio.on('request_data')
def handle_request_data():
    """处理数据请求"""
    print("收到数据请求")
    if latest_data:
        emit('update_colors', {'values': latest_data})
    else:
        emit('message', {'status': 'no_data', 'message': '暂无数据'})

@socketio.on('heartbeat')
def handle_heartbeat(data):
    """处理心跳请求"""
    emit('heartbeat_response', {'time': time.time()})

# 串口监听线程
def serial_listener():
    """
    持续监听串口，解析数据并通过 Socket.IO 推送到前端。
    """
    global ser, serial_connected, last_reconnect_time, latest_data
    
    while True:
        # 检查串口连接状态，如果断开则尝试重连
        if not serial_connected:
            current_time = time.time()
            if current_time - last_reconnect_time > 5:  # 每5秒尝试一次
                last_reconnect_time = current_time
                connect_serial()
            time.sleep(0.5)
            continue
            
        try:
            # 读取帧头
            frame_header = ser.read(3)
            if frame_header == b'\xAA\xAB\xAC':  # 检测帧头
                data = frame_header + ser.read(513)  # 读取完整帧
                if len(data) == 516:  # 验证数据帧长度
                    points = parse_data_frame(data)
                    if points:  # 如果解析成功
                        # 更新最新数据缓存
                        latest_data = points
                        
                        # 推送数据到前端
                        # 使用多种方式发送，确保兼容性
                        socketio.emit('update_colors', {'values': points})
                        socketio.send({'values': points})  # 发送普通消息
                        
                        print(f"Updated U-shape grid with values: {points}")
                        
                        # 设一个延迟，减少频率
                        time.sleep(0.1)
            
            # 保持原始延迟
            time.sleep(0.1)  # 延迟，避免占用过高的CPU
        except Exception as e:
            print(f"串口读取错误: {e}")
            serial_connected = False  # 标记连接断开
            time.sleep(0.5)


@app.route('/')
def index():
    """
    渲染前端 HTML 页面。
    """
    return render_template('index.html')  # 确保index.html在templates文件夹中


# 添加HTTP轮询API
@app.route('/api/data')
def get_data():
    """通过HTTP API获取最新数据"""
    if latest_data:
        return jsonify({'values': latest_data})
    else:
        return jsonify({'error': '暂无数据'}), 404


# 添加状态API端点
@app.route('/api/status')
def status():
    """返回串口连接状态"""
    return jsonify({"connected": serial_connected})


# 添加刷新API
@app.route('/api/reset')
def reset_serial():
    """手动重置串口连接"""
    global serial_connected
    serial_connected = False
    return jsonify({"status": "重置中", "success": True})


# 添加调试用API，生成测试数据
@app.route('/api/test_data')
def generate_test_data():
    """生成测试数据并发送"""
    import random
    test_data = [random.randint(100, 200) for _ in range(64)]
    socketio.emit('update_colors', {'values': test_data})
    return jsonify({"status": "已发送测试数据", "data": test_data})


if __name__ == '__main__':
    # 启动串口监听线程
    threading.Thread(target=serial_listener, daemon=True).start()
    # 运行Web服务器，禁用调试重载器以避免线程问题
    socketio.run(app, host='0.0.0.0', port=5000, debug=True, use_reloader=False)

