#!/usr/bin/env python3
"""
单进程一体化全链路联调测试（适配沙箱：所有组件在一个进程内，秒级完成）。
- 内置 amqtt broker (asyncio, 后台线程)
- 内置 mock 网页服务端 (HTTP, 后台线程): /api/camera/latest, /api/iot/state
- 内置 服务端 daemon: 订阅 v5/bag/# 镜像状态到 mock-web
- 内置 ESP32 模拟器: 严格复刻固件 MQTT/camera 协议契约
- 主线程跑《对接文档》第8节 6 步最小闭环断言 + 摄像头链路 + mode_switch
"""
import asyncio, os, threading, json, time, uuid, urllib.request, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import paho.mqtt.client as mqtt

BROKER_HOST, BROKER_PORT, WEB_PORT = "127.0.0.1", 18831, 18090
WEB = f"http://127.0.0.1:{WEB_PORT}"
# 自洽自测：sim 发的与 mock 校验的是同一个 TOKEN，留空即可全绿；真实联调时 export BAG_DEVICE_TOKEN=<令牌>
TOKEN = os.environ.get("BAG_DEVICE_TOKEN", "")
TS="v5/bag/status"; TSE="v5/bag/sensors"; TG="v5/bag/gps"; TC="v5/bag/cmd"; TA="v5/bag/cmd/ack"

state = {"status":None,"deviceOnline":False,"battery":None,"temp":None,"humid":None,
         "lat":None,"lng":None,"lastSeenAt":None}
snap = {"n":0,"ts":None}
slock = threading.Lock()
def now(): return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())

class H(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def j(self,c,o):
        b=json.dumps(o).encode(); self.send_response(c)
        self.send_header("Content-Type","application/json"); self.send_header("Content-Length",str(len(b)))
        self.end_headers(); self.wfile.write(b)
    def do_POST(self):
        n=int(self.headers.get("Content-Length",0)); raw=self.rfile.read(n)
        if self.path=="/api/camera/latest":
            if self.headers.get("x-device-token")!=TOKEN:
                return self.j(401,{"success":False,"message":"Unauthorized Device"})
            if b'name="image"' not in raw:
                return self.j(400,{"success":False,"message":"未找到图片文件"})
            with slock: snap["n"]=len(raw); snap["ts"]=now()
            return self.j(200,{"success":True,"message":"快照上传成功","timestamp":snap["ts"]})
        if self.path=="/_internal/state":
            with slock: state.update(json.loads(raw or b"{}"))
            return self.j(200,{"ok":True})
        self.j(404,{})
    def do_GET(self):
        if self.path=="/api/camera/latest":
            with slock: has=snap["n"]>0; sz=snap["n"]
            if has:
                self.send_response(200); self.send_header("Content-Type","image/jpeg")
                self.send_header("Content-Length",str(sz)); self.end_headers(); self.wfile.write(b"\xff"*sz); return
            return self.j(200,{"success":True,"hasSnapshot":False,"message":"暂无快照","timestamp":None})
        if self.path in ("/api/iot/state","/api/iot/status"):
            with slock: return self.j(200,dict(state))
        self.j(404,{})

def push(d):
    r=urllib.request.Request(WEB+"/_internal/state",data=json.dumps(d).encode(),
                             headers={"Content-Type":"application/json"})
    urllib.request.urlopen(r,timeout=3).read()

def run_broker():
    from amqtt.broker import Broker
    import logging; logging.disable(logging.CRITICAL)
    cfg={"listeners":{"default":{"type":"tcp","bind":f"{BROKER_HOST}:{BROKER_PORT}"}},
         "sys_interval":0,"auth":{"allow-anonymous":True},"topic-check":{"enabled":False}}
    loop=asyncio.new_event_loop(); asyncio.set_event_loop(loop)
    async def _boot():
        # amqtt 0.11 在 __init__ 里调用 get_running_loop()，必须在运行中的 loop 内构造
        b=Broker(cfg)
        await b.start()
    loop.run_until_complete(_boot()); loop.run_forever()

def run_daemon():
    c=mqtt.Client(client_id="server_daemon",protocol=mqtt.MQTTv311)
    def oc(cl,u,f,rc,p=None): cl.subscribe("v5/bag/#")
    def om(cl,u,m):
        try: d=json.loads(m.payload.decode())
        except Exception: d={}
        t=m.topic
        if t==TS: push({"status":d.get("status"),"deviceOnline":d.get("status")=="online","lastSeenAt":now()})
        elif t==TSE: push({"temp":d.get("temp"),"humid":d.get("humid"),"battery":d.get("battery"),"lastSeenAt":now()})
        elif t==TG: push({"lat":d.get("lat"),"lng":d.get("lng"),"lastSeenAt":now()})
    c.on_connect=oc; c.on_message=om
    connect_retry(c); c.loop_forever()

screen={"t_writing":"","t_replay":"","t_light":"OFF"}; led={"mode":"off"}
def upload():
    bd="ESP32BagCamBoundary"; jpeg=b"\xff\xd8\xff\xe0"+b"\x00"*1200+b"\xff\xd9"
    body=(f"--{bd}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"photo.jpg\"\r\n"
          f"Content-Type: image/jpeg\r\n\r\n").encode()+jpeg+f"\r\n--{bd}--\r\n".encode()
    r=urllib.request.Request(WEB+"/api/camera/latest",data=body,
        headers={"x-device-token":TOKEN,"Content-Type":f"multipart/form-data; boundary={bd}"})
    return json.loads(urllib.request.urlopen(r,timeout=5).read()).get("success")

def run_esp32():
    c=mqtt.Client(client_id="ESP32_S3_BAG_001",protocol=mqtt.MQTTv311)
    c.will_set(TS,json.dumps({"status":"offline"}))
    def oc(cl,u,f,rc,p=None):
        cl.publish(TS,json.dumps({"status":"online"})); cl.subscribe(TC)
    def om(cl,u,m):
        try: d=json.loads(m.payload.decode())
        except Exception: d={}
        cid=d.get("id",""); a=d.get("action",""); v=d.get("value",""); stt,msg=0,"OK"
        if a=="screen_text": screen["t_writing"]=v; screen["t_replay"]=v
        elif a=="mode_switch":
            if v=="focus_mode": led["mode"]="breathing_blue"; screen["t_writing"]="专注模式"
            elif v=="normal_mode": led["mode"]="breathing_green"; screen["t_writing"]="普通模式"
            else: stt,msg=1,"unknown mode"
        else: stt,msg=1,"unknown action"
        if cid: cl.publish(TA,json.dumps({"cmd_id":cid,"status":stt,"msg":msg}))
    c.on_connect=oc; c.on_message=om
    connect_retry(c); c.loop_start()
    time.sleep(0.5)
    c.publish(TSE,json.dumps({"battery":88,"temp":24.5,"humid":45.0}))
    time.sleep(0.2); c.publish(TG,json.dumps({"lat":31.230412,"lng":121.473701}))
    upload()
    while True:
        time.sleep(0.5); c.publish(TSE,json.dumps({"battery":88,"temp":24.5,"humid":45.0}))

def connect_retry(c, tries=30, delay=0.3):
    for _ in range(tries):
        try:
            c.connect(BROKER_HOST,BROKER_PORT,30); return True
        except Exception:
            time.sleep(delay)
    raise RuntimeError("broker connect failed after retries")

def gj(p): return json.loads(urllib.request.urlopen(WEB+p,timeout=5).read())
def wait(fn,t=12,i=0.25):
    e=time.time()+t
    while time.time()<e:
        try:
            if fn(): return True
        except Exception: pass
        time.sleep(i)
    return False
res=[]
def chk(n,ok,d=""):
    res.append((n,ok)); print(f"[{'PASS' if ok else 'FAIL'}] {n} {d}",flush=True); return ok

def main():
    threading.Thread(target=run_broker,daemon=True).start()
    srv=ThreadingHTTPServer(("127.0.0.1",WEB_PORT),H)
    threading.Thread(target=srv.serve_forever,daemon=True).start()
    time.sleep(2.5)
    threading.Thread(target=run_daemon,daemon=True).start()
    time.sleep(1.0)
    threading.Thread(target=run_esp32,daemon=True).start()

    print("=== 全链路联调测试（单进程一体化）===",flush=True)
    chk("步骤1 state接口可达(daemon已起)", wait(lambda: gj("/api/iot/state") is not None))
    chk("步骤2 设备 status=online 镜像", wait(lambda: gj("/api/iot/state").get("deviceOnline")==True),
        f"-> status={gj('/api/iot/state').get('status')}")
    chk("步骤3 sensors 镜像", wait(lambda: gj("/api/iot/state").get("temp") is not None),
        f"temp={gj('/api/iot/state').get('temp')} humid={gj('/api/iot/state').get('humid')} battery={gj('/api/iot/state').get('battery')}")
    chk("步骤4 gps 镜像", wait(lambda: gj("/api/iot/state").get("lat") is not None),
        f"lat={gj('/api/iot/state').get('lat')} lng={gj('/api/iot/state').get('lng')}")
    def cam():
        r=urllib.request.urlopen(WEB+"/api/camera/latest",timeout=5); return r.headers.get("Content-Type")=="image/jpeg"
    chk("附加 摄像头快照上传->拉取(image/jpeg)", wait(cam))

    cid=str(uuid.uuid4()); ack={"v":None}
    wc=mqtt.Client(client_id="web_client",protocol=mqtt.MQTTv311)
    def om(cl,u,m):
        try: d=json.loads(m.payload.decode())
        except Exception: return
        if m.topic==TA and d.get("cmd_id")==cid: ack["v"]=d
    wc.on_message=om; connect_retry(wc); wc.loop_start(); wc.subscribe(TA); time.sleep(0.4)
    MSG="记得带作业本"
    wc.publish(TC,json.dumps({"id":cid,"action":"screen_text","value":MSG}))
    chk("步骤5 screen_text 驱动屏幕 t_writing/t_replay",
        wait(lambda: screen["t_writing"]==MSG and screen["t_replay"]==MSG), f"t_writing='{screen['t_writing']}'")
    chk("步骤6 设备回 cmd/ack 且 cmd_id 匹配(命令闭环)", wait(lambda: ack["v"] is not None), f"ack={ack['v']}")

    cid2=str(uuid.uuid4()); ack2={"v":None}
    def om2(cl,u,m):
        try: d=json.loads(m.payload.decode())
        except Exception: return
        if m.topic==TA and d.get("cmd_id")==cid2: ack2["v"]=d
    wc.on_message=om2
    wc.publish(TC,json.dumps({"id":cid2,"action":"mode_switch","value":"focus_mode"}))
    chk("附加 mode_switch focus_mode 驱动呼吸蓝灯+ACK",
        wait(lambda: led["mode"]=="breathing_blue" and ack2["v"] is not None), f"led={led['mode']}")

    p=sum(1 for _,o in res if o); t=len(res)
    print(f"\n=== 结果: {p}/{t} 通过 ===",flush=True)
    for n,o in res: print(f"  {'✓' if o else '✗'} {n}",flush=True)
    return 0 if p==t else 1

if __name__=="__main__":
    sys.exit(main())
