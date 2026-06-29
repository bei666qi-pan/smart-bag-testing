#!/usr/bin/env python3
"""
模拟服务端守护进程：以 v5/bag/# 通配订阅 broker，把设备上报镜像到 mock-web 的状态
（等价于真实 lib/iot/redis-mqtt-daemon.ts 把数据写进 Redis bag:latest）。
"""
import json, sys, time, urllib.request
import paho.mqtt.client as mqtt

BROKER = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
PORT   = int(sys.argv[2]) if len(sys.argv) > 2 else 1883
WEB    = sys.argv[3] if len(sys.argv) > 3 else "http://127.0.0.1:8090"

def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())

def push(d):
    req = urllib.request.Request(WEB + "/_internal/state",
                                 data=json.dumps(d).encode(),
                                 headers={"Content-Type": "application/json"})
    try: urllib.request.urlopen(req, timeout=3).read()
    except Exception as e: print("[daemon] push err", e, flush=True)

def on_connect(c, u, f, rc, props=None):
    print(f"[daemon] connected rc={rc}, subscribing v5/bag/#", flush=True)
    c.subscribe("v5/bag/#")

def on_message(c, u, msg):
    try: data = json.loads(msg.payload.decode())
    except Exception: data = {}
    t = msg.topic
    print(f"[daemon] mirror <- {t}: {msg.payload.decode()[:120]}", flush=True)
    if t == "v5/bag/status":
        online = data.get("status") == "online"
        push({"status": data.get("status"), "deviceOnline": online, "lastSeenAt": now()})
    elif t == "v5/bag/sensors":
        push({"temp": data.get("temp"), "humid": data.get("humid"),
              "battery": data.get("battery"), "lastSeenAt": now()})
    elif t == "v5/bag/gps":
        push({"lat": data.get("lat"), "lng": data.get("lng"), "lastSeenAt": now()})

c = mqtt.Client(client_id="server_daemon", protocol=mqtt.MQTTv311)
c.on_connect = on_connect
c.on_message = on_message
c.connect(BROKER, PORT, 30)
c.loop_forever()
