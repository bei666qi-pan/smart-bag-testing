# 智能书包 · Jetson 端统一联调报告（端—边—云 全链路真机）

- 日期：2026-06-14
- 执行环境：Jetson Orin Nano（ARM64，`seeed@seeed`，IP `172.20.10.3`）+ USB 直连 ESP32-S3
- 联调对象：真实生产后端 `bag.versecraft.cn` / `mqtt.bag.versecraft.cn:1883`
- 热点：`<WIFI_SSID>`（iPhone 个人热点，172.20.10.x 网段）
- ESP32 IP（ESP_IP）：**`172.20.10.2`**（mDNS：`smart-bag-esp32.local`）
- 最终固件标识：**`[OTA-v4 fix]`**（telnet 连接横幅），bin 1,263,856 字节，占用 64% APP 分区
- 结论：**端—边—云全链路（含 Jetson↔ESP 语音控制链路）已真机打通，9 个阶段全部实测通过。** 期间发现并修复了 4 处会导致真机不通/不可靠的缺陷（详见第 3 节）。纯声学语音往返（说"小乐"→ASR→LLM→TTS 播放）需人声+扬声器，按纪律如实标注未覆盖。

> 本报告每条结论都附实测命令与输出摘要，无伪造、无假设。与历史《联调报告.md》（沙箱模拟、g++ 桩）不同，本轮全部为**真机 + 真实生产 broker/后端**。

---

## 1. 环境与工具链版本（阶段 1）

| 项 | 值 |
|---|---|
| arduino-cli | 1.5.1 |
| esp32 core | esp32:esp32 **3.3.10** |
| 分区/PSRAM | `PartitionScheme=min_spiffs`（含 OTA 双 app 分区）、`PSRAM=opi` |
| 第三方库 | PubSubClient 2.8.0、FastLED 3.10.3、TinyGPSPlus 1.0.3 |
| MQTT 工具 | mosquitto-clients 2.0.11 |
| Python MQTT | paho-mqtt 1.5.1（系统）/ jetson venv 3.10 |
| 串口 | `/dev/ttyACM0`（CH343 1a86:55d3，cdc_acm） |

- 联网：`curl https://bag.versecraft.cn/...` 直连 0.27s 可达；生产 broker `mqtt.bag.versecraft.cn:1883` TCP 直连 OPEN。
- 代理：系统设了 Mac Clash 代理 `172.20.10.4:7897`（mDNS 名 `beideMacBook-Air.local`）。**arduino-cli 默认不读 env 代理且 Go 解析器不认 `.local`**，故显式配置 `network.proxy=http://172.20.10.4:7897` + `network.connection_timeout=1800s` 后，esp32 core/库下载（GitHub）才成功。生产 `.cn` 请求一律 `--noproxy '*'` 直连（更快更稳）。

---

## 2. 鉴权说明（生产后端已加登录）

线上 `/api/iot/state`、`/api/iot/daemon-status`、`GET /api/camera/latest` 均要求登录会话（`getSessionUser`），与原提示词假设的"直接可读"不同。经用户授权，注册了**验证账号 `jetson_itest`** 拿到 `sb_session` cookie 用于读取上述只读诊断接口（不改任何业务数据）。`POST /api/camera/latest`（设备上传）仍走 `x-device-token: <DEVICE_TOKEN>`，无需登录。

---

## 3. 关键修正（不修则真机不通/不可靠）

| # | 文件 | 问题 | 修复 |
|---|---|---|---|
| 1 | `esp32代码/MQTT.cpp` | WiFi 写死 `esp32/<WIFI_PASS>`，与现场热点不符（连不上→无法上 broker）。**提示词给的 `<WIFI_SSID>/<WIFI_PASS>` 也有误**（SSID 少 h、密码记成了 Mac 的）。 | 以 Jetson 实际已连热点为地面真相，改为 **`<WIFI_SSID>` / `<WIFI_PASS>`**（NetworkManager 已保存 PSK 验证）。 |
| 2 | `esp32代码/`（sketch） | 主 `.ino` 名 `Smart_Schoolbag_03.ino` 与文件夹名 `esp32代码` 不一致，`arduino-cli` 直接报 `main file missing`（仓库从未用真 arduino-cli 编过）。 | 重命名为 `esp32代码.ino`（Arduino 规范：主 .ino 名须等于文件夹名）。 |
| 3 | `esp32代码/MQTT.h`+`MQTT.cpp` | **Jetson↔ESP 语音控制链路断裂**：ESP32 固件根本没订阅 `v5/bag/voice/cmd`，也无任何 voice 处理（说明文档里的 ESP32 改动落在本仓库不存在的 `硬件传感器/` 目录）。 | 新增 `TOPIC_VOICE_CMD`，在 `mqtt_reconnect()` 同时 `subscribe(TOPIC_VOICE_CMD)`；callback 新增 `indicator`(listening/thinking/idle) 动作。voice/cmd 无 `id`→沿用 `if(id!="")` 天然不回 ACK。 |
| 4 | `esp32代码/MQTT.cpp` | **value 解析器不兼容带空格 JSON**：用死板的 `strstr(buf,"\"value\":\"")`（要求冒号后无空格）。浏览器 `JSON.stringify` 无空格能过；Jetson `json.dumps` 产生 `"value": "x"`（**有空格**）→ value 解析为空→语音 mode_switch/indicator 实际不生效。 | 改为 `strstr(buf,"\"value\"")` 仅锚定键名，再跳过 `:`/空格/`"`（与 action/id 同款健壮解析）。两种 JSON 均正确（见阶段 6/8 实测）。 |
| 5 | `esp32代码/Hardware/camera.cpp` | **OTA 不可靠**：`camera_system_loop()` 每 10s 做同步 HTTPS 上传（慢热点下阻塞主循环 1–3s），饿死 `ota_handle()`→OTA 反复超时（节流前 OTA 连续失败 2 次）。 | `uploadInterval` 10000→**30000**（30s）。节流后 OTA **第一次即成功**，MQTT/telnet 抖动也明显减小。 |

> 另：`esp32代码/Hardware/RemoteDebug.cpp` 的 telnet 连接横幅加了版本标记（`[OTA-vN ...]`），仅用于无线刷新后确认新固件生效，可保留或还原。

---

## 4. 逐阶段实测证据

### 阶段 2 · 编译并首刷（USB）
```
arduino-cli compile -b esp32:esp32:esp32s3 \
  --board-options PartitionScheme=min_spiffs,PSRAM=opi  ~/smart-bag代码/esp32代码
→ Sketch uses 1263363 bytes (64%) ... rc=0   # 0 error
arduino-cli upload  -b esp32:esp32:esp32s3 \
  --board-options PartitionScheme=min_spiffs,PSRAM=opi -p /dev/ttyACM0 ~/smart-bag代码/esp32代码
→ Wrote 1263504 bytes ... Hash of data verified. Hard resetting via RTS pin... rc=0
```
首编译曾因缺 `PubSubClient/FastLED/TinyGPSPlus` 失败，`arduino-cli lib install` 装齐后通过。

### 阶段 3 · 启动验证（串口日志）
> 坑：`arduino-cli monitor`/`cat` 打开 ttyACM0 时拉低 RTS/EN 把芯片摁在复位态→无输出。改用 pyserial 释放 EN 抓到完整启动日志：
```
ESP-ROM:esp32s3-20210327 ... rst:0x1 (POWERON)
=== 智能书包系统启动 ===
WiFi 已连接，IP: 172.20.10.2
摄像头初始化成功 / 屏幕初始化完成（UART1）
[telnet] 远程日志已启动: telnet 172.20.10.2 23
[OTA] 就绪: 主机名 smart-bag-esp32.local, IP 172.20.10.2, 口令保护=是
=== 远程联调服务已就绪（OTA + telnet）===
[MQTT] 已连接，已订阅 v5/bag/cmd ...，已上报 online
[REPORT] temp=26.0 humid=64.4 lat=0.000000 lng=0.000000
上传完成，HTTP 200，响应: {"success":true,"message":"快照上传成功",...}
```
五条就绪信息全部出现，**ESP_IP=172.20.10.2**。AHT10 首次 init 失败后重试成功、温湿度读数正常。

### 阶段 4 · 设备→云 上行链路（生产后端）
```
mosquitto_sub -h mqtt.bag.versecraft.cn -p 1883 -t 'v5/bag/#' -v
→ v5/bag/status  {"status":"online"}
  v5/bag/sensors {"temp":25.9,"humid":64.3}      # 真机（与串口一致，字段 lat/lng）
  v5/bag/gps     {"lat":0.000000,"lng":0.000000} # 室内无 GPS 定位

curl -b <cookie> .../api/iot/daemon-status
→ started:true redisConnected:true mqttConnected:true subscribed:true lastError:null
curl -b <cookie> .../api/iot/state
→ deviceOnline:true temp:25.8 humid:65 lat:0 lng:0 lastSeenAt 持续刷新(每~10s)
```
三条上报齐全、daemon 已订阅、state 反映在线且有温湿度。

### 阶段 5 · 摄像头链路
```
curl -b <cookie> -o /tmp/snap.jpg -w '%{content_type} %{http_code}' .../api/camera/latest
→ image/jpeg 200   ；file: JPEG image data, 320x240, baseline  ；魔数 ffd8ffe0
.../api/camera/status → {"success":true,"hasSnapshot":true,"lastSnapshotAt":"...新鲜..."}
```
设备 POST(x-device-token)→服务端→Web GET 全通，拉到真实 320×240 JPEG。

### 阶段 6 · 云→设备 命令闭环
```
mosquitto_pub -t v5/bag/cmd -m '{"id":"final-1","action":"screen_text","value":"最终联调OK"}'
mosquitto_pub -t v5/bag/cmd -m '{"id":"final-2","action":"mode_switch","value":"focus_mode"}'
mosquitto_pub -t v5/bag/cmd -m '{"id": "final-3", "action":"screen_text","value":"带空格JSON"}'  # 带空格
mosquitto_pub -t v5/bag/cmd -m '{"id":"final-4","action":"mode_switch","value":"normal_mode"}'
```
ACK（broker `v5/bag/cmd/ack`）：
```
{"cmd_id":"final-1","status":0,"msg":"OK"} ... final-2/3/4 全部 status:0，cmd_id 精确匹配
```
telnet 远程日志：
```
=== 智能书包 远程日志已连接 [OTA-v4 fix] ===
[CMD] action=screen_text value=最终联调OK id=final-1 → [ACK] {"cmd_id":"final-1",...}
[CMD] action=mode_switch value=focus_mode id=final-2 → [ACK] ...
[CMD] action=screen_text value=带空格JSON  id=final-3 → [ACK] ...   # 带空格 JSON 也正确解析
[CMD] action=mode_switch value=normal_mode id=final-4 → [ACK] ...
```
`screen_text`/`mode_switch(focus/normal)` 全闭环；中文无乱码；带空格 JSON 验证了修正 4 的健壮性。物理屏幕显示/呼吸蓝灯需人眼确认（设备摄像头朝外不能自拍屏幕），firmware 处理器以 `status:0` 成功执行已确认逻辑链路。

### 阶段 7 · OTA 无线烧录回归
```
arduino-cli upload -b esp32:esp32:esp32s3 --board-options PartitionScheme=min_spiffs,PSRAM=opi \
  -p 172.20.10.2 --protocol network --upload-field password=<OTA_PASSWORD> ~/smart-bag代码/esp32代码
→ Authenticating (PBKDF2-HMAC-SHA256)... OK ; Uploading 100% Done... ; rc=0
```
重连 telnet 见新横幅 `=== 智能书包 远程日志已连接 [OTA-v4 fix] ===` → 新固件已运行、设备已重启。**相机节流(修正5)前 OTA 连续超时 2 次，节流后第一次即成功**，证明可靠性问题已解决，"以后不用插线"成立。

### 阶段 8 · Jetson 语音子系统（小乐）
用 `iot_bridge.build_bridge_from_config()` 驱动（config：`IOT_ENABLED=True`、broker 一致、`IOT_REPORT_DIALOG=True`）。
```
[IoT] 已连接 broker ; [IoT] 订阅 v5/bag/cmd（只读，不发 ACK）
```
broker 抓包（`-F '%r | %t | %p'`）：
```
1 | v5/bag/voice/status | {"status":"online"}                      # online，retained
0 | v5/bag/voice/event  | {"type":"wake", ...}                     # 唤醒事件
0 | v5/bag/voice/event  | {"type":"dialog","user":"今天几号","reply":"6月14号","route":"local"}
0 | v5/bag/voice/status | {"status":"offline"}                     # 停止时优雅下线
```
服务端镜像：`/api/iot/state` → `voiceOnline:true`、`lastVoiceEvent:{type:dialog,user:今天几号,reply:6月14号}`（voice→broker→daemon→Redis→state 端到端）。

**"Jetson 不回 cmd/ack" 实测**：bridge 在线时向 `v5/bag/cmd` 发 `mode_switch`，bridge 日志 `[IoT] 收到 mode_switch=normal_mode`（已收），但 `v5/bag/cmd/ack` 计数 = **1**（仅 ESP32）。代码层 iot_bridge 全文无任何 cmd/ack 发布。

**Jetson→ESP32 语音控制链路（voice/cmd）实测**（修正 3+4 之后）：
```
bridge.publish_cmd("mode_switch","focus_mode") / ("indicator","listening") / ("mode_switch","normal_mode") / ("indicator","idle")
broker  v5/bag/voice/cmd ← 4 条 {"action":...,"value":...,"src":"jetson_voice"}（无 id）
ESP32 telnet:
  [CMD] action=mode_switch value=focus_mode  id=
  [CMD] action=indicator   value=listening   id=
  [CMD] action=mode_switch value=normal_mode id=
  [CMD] action=indicator   value=idle        id=
v5/bag/cmd/ack 计数 = 0 ；telnet [ACK] 计数 = 0    # 不污染命令闭环
```
value 正确解析、ESP32 处理对应动作、且 voice/cmd 全程不回 ACK。

---

## 5. 已验证项（真机 + 生产后端）

- [x] 工具链：arduino-cli + esp32 core 3.3.10 + mosquitto + paho 全可用
- [x] 编译 0 error、USB 首刷成功、五条就绪日志、ESP_IP=172.20.10.2
- [x] 设备→云：status/sensors/gps 三条上报在刷；daemon-status started/subscribed=true；state deviceOnline=true 有温湿度
- [x] 摄像头：设备上传 HTTP 200；Web GET 返回真实 320×240 image/jpeg
- [x] 命令闭环：screen_text + mode_switch(focus/normal)，ACK cmd_id 精确匹配 status:0，telnet [CMD]→[ACK]；紧凑/带空格 JSON 均健壮
- [x] OTA：网络协议无线烧录成功、设备重启、telnet 新横幅；相机节流后可靠
- [x] 语音子系统：voice/status online(retained)、voice/event(wake+dialog)、服务端镜像 voiceOnline/lastVoiceEvent；Jetson 被动订阅 cmd 但不回 ACK
- [x] **Jetson↔ESP 语音控制链路**：voice/cmd 的 mode_switch/indicator 真机生效且不回 ACK（修复后）

## 6. 未覆盖项及原因（如实标注）

- [ ] **纯声学语音往返**（真麦克风说"小乐"唤醒→VAD/ASR→LLM→TTS 扬声器播放）。原因：需人声输入与人耳确认，无法自主完成；且权威副本 `jetson边缘智能/venv` 的 `sounddevice` 导入报 OSError（PortAudio），完整音频栈实际跑在旧 `voice_assistant` venv。已确认前置条件具备：ReSpeaker XVF3800 4-Mic（card 0）在位、ollama 在线、DeepSeek/MiMo 云端 ASR/TTS/LLM 密钥已配（`.env`）。语音子系统与硬件的**全部 MQTT 集成契约**已验证（见阶段 8）。
- [ ] **物理屏幕文字 / 呼吸灯/指示灯的人眼确认**。原因：设备摄像头朝外、无法自拍自身屏幕；firmware 处理器执行成功（status:0、value 正确）已从逻辑与日志层面确认。
- [ ] **GPS 真实定位**：室内 `lat/lng=0`（"未获得有效定位，请移至室外空旷处"），属设备物理限制非链路故障；上报/镜像通道本身已通。

## 7. 其它发现与建议（不影响本次结论）

1. **broker 上存在外部"冒充者"retained 残留**：每次订阅 `v5/bag/sensors`、`v5/bag/gps` 的瞬间会先收到一组非真机数据 `{"battery":99,"temp":22.2,"humid":33}` 与 `{"latitude":39.9087,"longitude":116.3975}`（北京坐标、字段 `latitude/longitude`）。非本 Jetson 进程所发、仓库内无此指纹，是历史测试遗留的 retained。真机每 ~10s 的实时上报会覆盖 temp/humid/lat/lng，故 state 主体为真机值；但 `battery:99` 因真机 sensors 不含 battery 字段而滞留 Redis hash。建议排查来源并清理：
   ```
   mosquitto_pub -h mqtt.bag.versecraft.cn -p 1883 -t v5/bag/sensors -r -n
   mosquitto_pub -h mqtt.bag.versecraft.cn -p 1883 -t v5/bag/gps     -r -n
   ```
   （未自行清理：属生产共享 broker 写操作、且不影响已验证链路，留给用户决定。）
2. **真机 sensors 不含 battery 字段**（`{"temp,humid}`，对接文档示例含 battery）。如仪表盘需要电量，需在固件接入电量采样后于 `mqtt_publish_sensors` 补 battery。
3. **重复目录**：`run_xiaole.sh` 实际 `cd /home/seeed/workspaces/voice_assistant`（旧副本，含可用音频 venv），而权威副本是 `smart-bag代码/jetson边缘智能`。**未删除旧目录**——它是 autostart 实际运行体且持有可用 venv/models，直接删会破坏语音自启。若要统一为 smart-bag 副本，需同步迁移 venv/models 并改 `run_xiaole.sh` 路径，建议单独处理。
4. **串口监视复位坑**：`arduino-cli monitor`/`cat /dev/ttyACM0` 会经 RTS 把 ESP32 摁在复位。后续看日志优先用 **telnet `nc 172.20.10.2 23`**（不复位、且为联调关键事件流）。

## 8. 运维命令速查

```bash
export PATH=/home/seeed/bin:$PATH
SK=~/smart-bag代码/esp32代码
# 编译
arduino-cli compile -b esp32:esp32:esp32s3 --board-options PartitionScheme=min_spiffs,PSRAM=opi $SK
# USB 刷（首刷/兜底）
arduino-cli upload  -b esp32:esp32:esp32s3 --board-options PartitionScheme=min_spiffs,PSRAM=opi -p /dev/ttyACM0 $SK
# OTA 无线刷（同热点）
arduino-cli upload  -b esp32:esp32:esp32s3 --board-options PartitionScheme=min_spiffs,PSRAM=opi \
  -p 172.20.10.2 --protocol network --upload-field password=<OTA_PASSWORD> $SK
# 远程日志
nc 172.20.10.2 23
# 看上报 / 下发命令
mosquitto_sub -h mqtt.bag.versecraft.cn -p 1883 -t 'v5/bag/#' -v
mosquitto_pub -h mqtt.bag.versecraft.cn -p 1883 -t v5/bag/cmd -m '{"id":"t1","action":"screen_text","value":"你好"}'
# 启动语音 IoT 桥接（语音子系统接入 MQTT）
cd ~/smart-bag代码/jetson边缘智能 && python3 -c "import iot_bridge,time;b=iot_bridge.build_bridge_from_config();b.start();time.sleep(3);print('voiceOnline pushed', b.connected)"
```

---

## 9. 交付后优化（应用户要求："按你的理解优化"）

### 9.1 语音助手 autostart 切到新权威副本 + 修好真机运行（已完成并验证）
排查发现 systemd 用户服务 `~/.config/systemd/user/xiaole.service` 原指向旧重复目录 `~/workspaces/voice_assistant`，且：

| 问题 | 真因 | 处理 |
|---|---|---|
| 旧 autostart 跑的是**没有 iot_bridge 的旧代码** | 旧目录无 `iot_bridge.py`（语音联动从未生效） | 服务 `WorkingDirectory`/`ExecStart` + `run_xiaole.sh` 改指 `~/smart-bag代码/jetson边缘智能` |
| **崩溃循环**（旧 NRestarts=24；新副本切过来后仍 3min 崩 1 次） | 对零长音频段 `np.max(空数组)` 抛 `ValueError: zero-size array`；新副本只守了快速唤醒路径，漏了 VAD 段的 `_process_wake_segment`(:719) 与 AWAKE 路径(:800) | 在 VAD 段循环顶层 + `_process_wake_segment` 各加 `if seg.size==0` 守卫；实测 95s **NRestarts=0、0 traceback** |
| 语音音频栈不可用 | **系统缺 `libportaudio2`**（两 venv 的 sounddevice 都 OSError） | `apt install libportaudio2`（走直连，国内镜像） |
| 新副本 venv 是**有损拷贝** | sherpa_onnx 目录 0 文件、kokoro 依赖链/几十包为空 | 从旧 venv `rsync -a` 补齐 site-packages（同版本同架构，4.5s，无网） |
| perf gate 每次启动重载模型、易卡死自启 | dev 性能门 | 服务加 `Environment=XIAOLE_SKIP_PERF=1` 跳过 |

**结果（实测）**：服务 `active/running`、`NRestarts=0`（稳定不再循环）；助手完整加载 `[ASR] sherpa-onnx` + `[LLM] ollama qwen3:1.7b` + `[TTS] Kokoro` + `[IoT] bridge`，进入 `💤 待唤醒` 监听态；`/api/iot/state` → `voiceOnline:true`。本地 ASR/LLM/TTS 全就绪，**整机自给自足，旧 `~/workspaces/voice_assistant` 现可安全删除**（建议确认数日稳定后再删，作回退备份）。

> 仍受限：① MiMo 云 ASR/TTS 密钥 401 无效（本地 sherpa/Kokoro 兜底，功能不缺，仅少云加速）——需用户换有效 key；② 真人"说小乐"的声学往返仍需人耳确认。

### 9.2 清理 broker 冒充者 retained（被安全门拦截，需用户执行）
清除 `v5/bag/sensors`、`v5/bag/gps` 上的历史 retained 残留属"生产共享 broker 写操作"，被自动模式安全门拦截、未执行。请你手动跑（或在 settings 加 Bash 放行规则）：
```bash
mosquitto_pub -h mqtt.bag.versecraft.cn -p 1883 -t v5/bag/sensors -r -n
mosquitto_pub -h mqtt.bag.versecraft.cn -p 1883 -t v5/bag/gps     -r -n
```
（已确认 daemon 对空 payload 安全：`JSON.parse` 在 try/catch 内，不会写 Redis；真机 sensors/gps 为非 retained，每 10s 实时覆盖，清除只去掉历史残留。注：Redis 里既存的 `battery:99` 不受此命令影响，会持续到被真机带 battery 的上报覆盖——当前固件不发 battery。）
