# 智能书包 · Jetson 端统一联调任务（整段粘贴给 Jetson 上的 Claude Code）

你是跑在 **Jetson（ARM64 Linux）** 上的工程联调助手。一台 ESP32-S3 已通过 USB 接在本机。
你的目标：完成智能书包"端—边—云"**全链路统一联调**，并且**亲自用真实测试逐一确认每个环节打通后才交付**。
任何一环没验证通过，就继续排查修复，不许伪造、不许假设结果、不许跳过。允许你修改代码并重新烧录。

---

## 一、现状与环境（重要）

- 本机 = Jetson；ESP32-S3 **USB 直连本机**，串口通常是 `/dev/ttyACM0`（也可能 `/dev/ttyUSB0`）。
- 用户从 Mac 通过 SSH 连到本机操作你。
- **最新代码目前在 Mac 上，本机可能还没有。** 项目根目录名 `smart-bag代码`，含四块：
  `esp32代码/`、`jetson边缘智能/`、`网页服务端代码/`、`物联网设计大赛文档/`。
- 拿到代码后**必读**这几份（以它们为准，别另起炉灶）：
  - `网页服务端代码/软硬件对接文档.md` —— 对接契约唯一权威
  - `联调测试/远程联调操作手册.md` —— OTA / telnet / 烧录命令
  - `联调测试/联调报告.md` —— 已修复点与已验证项
  - `联调测试/selftest_oneshot.py` —— 本地全链路自测脚本（离线兜底用）

## 二、契约速记（细节以对接文档为准）

- 硬件 MQTT：`mqtt.bag.versecraft.cn:1883`（TCP）。
- Topic：设备上报 `v5/bag/status`、`v5/bag/sensors`、`v5/bag/gps`、`v5/bag/cmd/ack`；设备订阅 `v5/bag/cmd`。
- 设备在线**只认** `v5/bag/status = {"status":"online"}`。
- 下行命令 `{"id","action","value"}`；ACK 必须 `{"cmd_id","status","msg"}` 且 `cmd_id==id`。
- 摄像头快照：`POST https://bag.versecraft.cn/api/camera/latest`，Header `x-device-token: <DEVICE_TOKEN>`，multipart 字段名 `image`。
- 服务端状态：`GET https://bag.versecraft.cn/api/iot/state`、`/api/iot/daemon-status`。
- Jetson 语音子系统：发 `v5/bag/voice/status`、`v5/bag/voice/event`；对 `v5/bag/cmd` **只被动订阅、绝不回 ACK**。
- ESP32 固件已内置 OTA（主机名 `smart-bag-esp32`，口令 `<OTA_PASSWORD>`）与 telnet 日志（端口 23）。
- 编译板型 `esp32:esp32:esp32s3`，**分区方案必须带 OTA（用 `min_spiffs`）**，`PSRAM=opi`。

---

## 三、分阶段执行（用 TODO 跟踪；每阶段验证通过才进入下一阶段）

### 阶段 0 · 取得代码
先查本机是否已有项目（如 `~/smart-bag代码`，或问用户路径）。若没有：
本机执行 `whoami; echo $HOME; hostname -I` 拿到用户名/家目录/IP，然后**打印一条让用户去 Mac 上执行的命令**（不是在你这执行），例如：
```
rsync -avz --exclude node_modules --exclude .git \
  "/Users/qi/Desktop/smart-bag代码" <jetson用户>@<jetson_ip>:~/
```
（`jetson边缘智能/models` 体积大，若本机已有可加 `--exclude jetson边缘智能/models`。）
等用户确认拷完、你能在本机看到代码，再继续。

### 阶段 1 · 工具链
- 确认联网：`curl -I https://bag.versecraft.cn/api/iot/daemon-status`、`ping -c1 downloads.arduino.cc`。
- 装 `arduino-cli`（若无）→ `arduino-cli core update-index` → `arduino-cli core install esp32:esp32`。
- 装联调工具：`mosquitto-clients`（mosquitto_pub/sub）、`python3-paho-mqtt`。
- 通过标准：`arduino-cli`、`esp32` core、`mosquitto_sub` 都可用。

### 阶段 2 · 编译并首刷 ESP32（USB）
- **先核对热点**：看 `esp32代码/MQTT.cpp` 顶部 `WIFI_SSID/WIFI_PASS` 是否与现场热点一致，且该热点能上外网（要连 broker）。不一致就改好再刷。
- 识别串口：`arduino-cli board list` 或 `ls /dev/ttyACM* /dev/ttyUSB*`；权限不足把用户加入 `dialout` 组或用 `sudo`。
- 编译：
  ```
  arduino-cli compile -b esp32:esp32:esp32s3 \
    --board-options PartitionScheme=min_spiffs,PSRAM=opi \
    ~/smart-bag代码/esp32代码
  ```
- 烧录：`arduino-cli upload -b esp32:esp32:esp32s3 -p <port> ~/smart-bag代码/esp32代码`
- 通过标准：编译 0 error、上传成功。（串口监视没输出时，再试加 `CDCOnBoot=cdc` 板选项。）

### 阶段 3 · 启动验证（串口日志）
- `arduino-cli monitor -p <port> -c baudrate=115200` 抓启动日志。
- 通过标准：依次看到 `WiFi 已连接`+IP、`[MQTT] 已连接`、`[telnet] 远程日志已启动`、`[OTA] 就绪`、`远程联调服务已就绪`。**记下 ESP_IP**。
- WiFi 连不上 → 回阶段 2 核对热点 SSID/密码。

### 阶段 4 · 设备→云 上行链路（用真实生产后端）
- `mosquitto_sub -h mqtt.bag.versecraft.cn -p 1883 -t 'v5/bag/#' -v`，确认真实 ESP32 的 status/sensors/gps 在刷。
- `curl -s https://bag.versecraft.cn/api/iot/daemon-status` → `started`/`subscribed` 为 true。
- `curl -s https://bag.versecraft.cn/api/iot/state` → `deviceOnline:true` 且 temp/humid/lat/lng 有值。
- 通过标准：三条上报都在、服务端 state 反映在线且有数。

### 阶段 5 · 摄像头链路
- `curl -s -o /tmp/snap.jpg -w '%{content_type} %{http_code}\n' https://bag.versecraft.cn/api/camera/latest`
- 通过标准：返回 `image/jpeg`（设备已上传过快照）；`/tmp/snap.jpg` 非空。

### 阶段 6 · 云→设备 命令闭环
- 后台订阅 ACK：`mosquitto_sub -h mqtt.bag.versecraft.cn -p 1883 -t v5/bag/cmd/ack -v &`
- 下发：`mosquitto_pub -h mqtt.bag.versecraft.cn -p 1883 -t v5/bag/cmd -m '{"id":"itest-1","action":"screen_text","value":"记得带作业本"}'`
- 通过标准：收到 `cmd_id=itest-1`、`status:0` 的 ACK；telnet 日志出现 `[CMD]`→`[ACK]`；**屏幕真的显示该文字**。
- 再测 `mode_switch`/`focus_mode`：确认 ACK + 呼吸蓝灯。

### 阶段 7 · OTA 无线烧录回归（证明以后不用插线）
- 改一处可见日志（如启动横幅），无线刷一次：
  `arduino-cli upload -b esp32:esp32:esp32s3 -p <ESP_IP> --upload-field password=<OTA_PASSWORD> ~/smart-bag代码/esp32代码`
  （不行就用 espota.py：`python3 <core路径>/tools/espota.py -i <ESP_IP> -p 3232 -a <OTA_PASSWORD> -f <编译出的.bin>`）
- 通过标准：OTA 成功、设备重启、telnet 看到新日志。

### 阶段 8 · Jetson 语音子系统（小乐）
- 读 `jetson边缘智能/config.py` 的 `IOT_*`；`pip install paho-mqtt`；按 `jetson边缘智能/语音联动说明.md` 启动 iot_bridge / 语音助手。
- 通过标准：broker 上出现 `v5/bag/voice/status` online；唤醒/对话时 `v5/bag/voice/event` 有事件；并确认 Jetson **没有**对 `v5/bag/cmd` 回 ACK。
- 若麦克风/音频不具备：至少验证 bridge 连上 broker 且 voice/status 在线，其余如实标注未覆盖。

### 阶段 9 · 交付
全部通过后，写 `联调测试/Jetson统一联调报告.md`：逐环节列**实测证据**（命令 + 输出摘要）、ESP_IP、固件版本、已验证项、未覆盖项及原因。**任一环没过，禁止写"已打通"。**

---

## 四、纪律
- 真实测试，不伪造、不假设；每条结论都要有命令输出支撑。
- 卡住就排查（telnet/串口日志、daemon-status、网络可达性），别绕过。
- 生产 broker 不可达时：本机起本地 broker，用 `联调测试/selftest_oneshot.py` 跑软件链路兜底，并在报告里**明确写这是本地验证、非生产**。
- 多用 TODO 列表，分阶段汇报进度。
