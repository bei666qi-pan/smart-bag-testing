#!/usr/bin/env python3
"""
模拟 ESP32 固件行为，严格复刻 MQTT.cpp / camera.cpp 的协议契约：
  - 连接时 LWT={"status":"offline"}，上线发 {"status":"online"}（对应 mqtt_reconnect）
  - 订阅 v5/bag/cmd（对应 client.subscribe(TOPIC_CMD)）
  - 周期上报 sensors / gps（对应 mqtt_publish_sensors / mqtt_publish_gps）
  - 上传摄像头快照到 /api/camera/latest（对应 camera_upload_photo，多部分表单 + x-device-token）
  - 收到 cmd 后按 action 驱动「屏幕/灯」并回 ACK，字段必须叫 cmd_id（对应 callback）
模拟屏幕状态用内存变量表示，便于断言 screen_text 是否真正上屏。
"""
import json, os, sys, time, threading, urllib.request, uuid
import paho.mqtt.client as mqtt

BROKER = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
PORT   = int(sys.argv[2]) if len(sys.argv) > 2 else 1883
WEB    = sys.argv[3] if len(sys.argv) > 3 else "http://127.0.0.1:8090"
# 真实联调（打 bag.versecraft.cn）时 export BAG_DEVICE_TOKEN=<令牌>；本地自测留空即可（与 mock 自洽）
DEVICE_TOKEN = os.environ.get("BAG_DEVICE_TOKEN", "")

TOPIC_STATUS="v5/bag/status"; TOPIC_SENSORS="v5/bag/sensors"
TOPIC_GPS="v5/bag/gps"; TOPIC_CMD="v5/bag/cmd"; TOPIC_CMD_ACK="v5/bag/cmd/ack"

# 模拟屏幕控件 + 灯状态（用于断言命令真正驱动了硬件）
screen = {"t_writing": "", "t_replay": "", "t_light": "OFF", "t_temperature":"", "t_humidity":"", "t_gps":""}
led_mode = "off"

def upload_snapshot():
    boundary = "ESP32BagCamBoundary"
    jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 1200 + b"\xff\xd9"  # 假 JPEG，约 1.2KB
    body  = (f"--{boundary}\r\n"
             'Content-Disposition: form-data; name="image"; filename="photo.jpg"\r\n'
             "Content-Type: image/jpeg\r\n\r\n").encode() + jpeg + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(WEB + "/api/camera/latest", data=body, headers={
        "x-device-token": DEVICE_TOKEN,
        "Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=5).read())
        print(f"[esp32] 快照上传 -> {resp.get('message')} ({len(jpeg)}B)", flush=True)
        return resp.get("success", False)
    except Exception as e:
        print("[esp32] 快照上传失败", e, flush=True); return False

def on_connect(c, u, f, rc, props=None):
    print(f"[esp32] connected rc={rc}", flush=True)
    c.publish(TOPIC_STATUS, json.dumps({"status":"online"}))
    c.subscribe(TOPIC_CMD)
    print("[esp32] 已上线并订阅 v5/bag/cmd", flush=True)

def on_message(c, u, msg):
    global led_mode
    try: data = json.loads(msg.payload.decode())
    except Exception: data = {}
    cid = data.get("id",""); action=data.get("action",""); value=data.get("value","")
    print(f"[esp32] 收到命令 action={action} value={value} id={cid}", flush=True)
    status, m = 0, "OK"
    if action == "screen_text":
        screen["t_writing"] = value; screen["t_replay"] = value   # updateWritingText + updateAIResponse
        print(f"[esp32] >> 屏幕 t_writing/t_replay = '{value}'", flush=True)
    elif action == "mode_switch":
        if value == "focus_mode": led_mode="breathing_blue"; screen["t_writing"]="专注模式"
        elif value == "normal_mode": led_mode="breathing_green"; screen["t_writing"]="普通模式"
        else: status, m = 1, "unknown mode"
        print(f"[esp32] >> 灯效={led_mode}", flush=True)
    else:
        status, m = 1, "unknown action"
    if cid:
        ack = {"cmd_id": cid, "status": status, "msg": m}   # 字段必须叫 cmd_id
        c.publish(TOPIC_CMD_ACK, json.dumps(ack))
        print(f"[esp32] >> 回复 ACK {ack}", flush=True)

c = mqtt.Client(client_id="ESP32_S3_BAG_001", protocol=mqtt.MQTTv311)
c.will_set(TOPIC_STATUS, json.dumps({"status":"offline"}), qos=0, retain=False)
c.on_connect = on_connect
c.on_message = on_message
c.connect(BROKER, PORT, 30)
c.loop_start()

def reporter():
    t=24.5; h=45.0; lat=31.230412; lng=121.473701; batt=88
    while True:
        c.publish(TOPIC_SENSORS, json.dumps({"battery":batt,"temp":t,"humid":h}))
        screen["t_temperature"]=f"温度: {t}°C"; screen["t_humidity"]=f"湿度: {h}%"
        print(f"[esp32] 上报 sensors temp={t} humid={h} battery={batt}", flush=True)
        time.sleep(1)
        c.publish(TOPIC_GPS, json.dumps({"lat":lat,"lng":lng}))
        screen["t_gps"]=f"GPS: {lat},{lng}"
        print(f"[esp32] 上报 gps {lat},{lng}", flush=True)
        upload_snapshot()
        time.sleep(9)

threading.Thread(target=reporter, daemon=True).start()
# 暴露 screen 状态供测试断言：写到文件
def dump_state():
    while True:
        with open(sys.argv[4] if len(sys.argv)>4 else "/tmp/esp32_screen.json","w") as fp:
            json.dump({"screen":screen,"led_mode":led_mode}, fp, ensure_ascii=False)
        time.sleep(0.3)
threading.Thread(target=dump_state, daemon=True).start()
while True: time.sleep(1)
