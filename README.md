# 仓眼 · CangYan

**基于 K1（RISC-V）的边缘视觉监测终端** —— 摄像头实时识别目标，进入画定禁区即当场告警并留痕，断网照常工作，本地网页看板可查状态与历史。

> 参赛项目：第二届 CIE 全国 RISC-V 高水平创新及应用大赛 · CIE-进迭时空赛道
> 赛题：基于 K1 的嵌入式终端及边缘 AI 应用开发

---

## 1. 它是什么

不是"跑了个 YOLO"，而是一台**能独立上电运行的终端**：

| 演示动作 | 证明了什么 |
|---|---|
| 插电 → 约 30 秒后网页出现「运行中」与帧率 | 它是终端，不是脚本 |
| 手伸进禁区 → 3 秒内网页顶部变红、事件 +1 | 有判定逻辑，不是单纯识别 |
| 手拿出 → 约 1 秒后红条消失，事件记下持续时长 | 有状态机，不是每帧都报警 |
| 拔网线 2 分钟 → 插回，事件列表里能看到断网期间那几条 | 端侧闭环，不依赖云 |

---

## 2. 架构

```
USB 摄像头 (/dev/video20, MJPG 1280x720)
        │  V4L2
        ▼
detection_stream_demo          ← 官方 C++ 程序
        │                        YOLOv6n INT8，走 SpacemiT ONNX Runtime EP
        │                        （日志里的 "Enable spacemit ep now" 即 NPU 加速生效）
        │  逐帧输出文本：
        │  bbox[ 0] x1y1x2y2: ( 424, 0, 638, 476), score: 0.435, label_text: person
        ▼
monitor.py                     ← 本项目唯一自研代码，零第三方依赖
   ├─ reader_thread()   解析 bbox 行，按 "bbox[ 0]" 切分帧
   ├─ process_frame()   框中心 → 射线法判多边形禁区 → 连续帧确认/解除
   ├─ idle_watchdog()   无目标时补空帧，保证告警能正常解除（见第 6 节）
   ├─ write_event()     追加写 events.jsonl
   └─ Handler           http.server，/state 与 /events 两个接口 + 自刷新看板
        │
        ▼
   网页看板 http://板子IP:8080（1 秒轮询）
```

**关键决策：不重写检测程序。** 官方 C++ demo 已跑通且走 NPU 加速，本项目只做上层应用 —— 解析输出、加业务判定、做看板、做自启。

### RISC-V 技术落点

| 位置 | 说明 |
|---|---|
| NPU 推理 | YOLOv6n INT8 量化模型经 SpacemiT ONNX Runtime EP 调度到 K1 的 2 TOPS NPU |
| 异构算力分工 | 8 核 RISC-V CPU 负责取图、业务判定、HTTP 服务；NPU 专职推理 |
| 全栈原生 RISC-V | 系统、驱动、Python 运行时、HTTP 服务全部为 riscv64 原生，非交叉移植产物 |

---

## 3. 硬件与运行环境

| 项 | 值 |
|---|---|
| 开发板 | MUSE Pi Pro（K1，8 核 RISC-V，2 TOPS NPU，8 GB） |
| 系统 | Bianbu 2.3.5（Ubuntu 24.04 noble 基座），内核 6.6.63 |
| 存储 | eMMC 58 GB |
| 摄像头 | USB UVC（Realtek 0bda:5846），MJPG 1280×720@30，节点 `/dev/video20` |
| 推理程序 | `/usr/bin/detection_stream_demo` |
| 模型 | `/usr/share/ai-support/models/yolov6.json` → `yolov6p5_n.q.onnx`（INT8） |
| 第三方依赖 | **无**，只用 Python 标准库 |

---

## 4. 部署

```bash
# 1) 拷到板子
scp -r cangyan dwb@<板子IP>:/home/dwb/

# 2) 先手工跑一次，确认画面和看板都正常
cd /home/dwb/cangyan
bash run.sh
# 浏览器打开 http://<板子IP>:8080

# 3) 确认无误后装成开机自启
bash deploy/install.sh
```

`deploy/install.sh` 会做三件事：安装 systemd 服务、打开 GDM 自动登录（否则上电停在登录界面，服务等不到图形会话）、重启并自检。

取消自启：`bash deploy/uninstall.sh`

### 常用命令

```bash
journalctl -u cangyan -f                      # 实时日志
sudo systemctl restart cangyan                # 重启
curl -s http://127.0.0.1:8080/state           # 看实时状态
curl -s http://127.0.0.1:8080/events | tail   # 看事件
```

---

## 5. 配置（config.json）

| 字段 | 当前值 | 说明 |
|---|---|---|
| `demo_bin` | `/usr/bin/detection_stream_demo` | 官方检测程序 |
| `model` | `/usr/share/ai-support/models/yolov6.json` | 模型配置 |
| `camera` | `/dev/video20` | 换 USB 口会变，用 `v4l2-ctl --list-devices` 查 |
| `frame_w` / `frame_h` | `640` / `480` | **检测输出坐标基准，不是相机分辨率**，填错禁区全错 |
| `zone` | 四点多边形 | 禁区，归一化 0~1，顺时针写 |
| `confirm_frames` | `8` | 连续 8 帧命中才告警 |
| `release_frames` | `15` | 连续 15 帧未命中才解除 |
| `watch_classes` | `["person"]` | 只关注人；空数组表示所有类别 |
| `min_score` | `0.45` | 置信度阈值 |
| `http_port` | `8080` | 看板端口 |
| `idle_timeout_s` | `0.8` | 看门狗超时，见第 6 节 |

---

## 6. 两个必须知道的坑

### ① 检测坐标是 640×480，不是 1280×720

检测程序输出的框坐标基于 640×480，而相机实际分辨率是 1280×720。
`frame_w` / `frame_h` 必须填 640/480，否则归一化坐标算错，禁区判定整个偏掉。

### ② 画面里没有目标时，demo 一行都不打印

按 `bbox[ 0]` 切帧的写法有个致命副作用：**无人时没有任何 bbox 行 → 帧判定函数不被调用 → 未命中计数永不增长 → 告警永远不解除**。

`idle_watchdog()` 就是为这个存在的：超过 `idle_timeout_s` 没有新的完整帧，就补一个空帧；若当时正在告警，一次性补满 `release_frames` 帧，保证红条在约 1 秒内消失。

> 这个行为已用仿真 demo 验证：持续输出 3 秒后完全静默，告警在静默约 1.3 秒后自动解除，`events.jsonl` 正确记录 `duration_s`。

### ③ 必须加 `-f`，否则窗口闪一下就退

开着预览窗口时，demo 的预览线程和检测线程会各自打开一次摄像头，第二次 `open` 把第一次的视频流顶掉：

```
VIDIOC_REQBUFS: errno=19 (No such device)
```

表现为点桌面图标后"看到画面 → 瞬间退出"。加 `-f` 关掉预览后实测连续 12 秒 259 个检测框、零报错。

### ④ 摄像头参数要写索引 `0`，不能写 `/dev/video20`

```
detection_stream_demo -f yolov6.json 0            # ✅
detection_stream_demo -f yolov6.json /dev/video20 # ❌ Dataloader init error
```

传索引时程序会自己探测正确的 camera id；传路径会被当成"视频文件"处理。

---

## 7. 接口

| 路径 | 方法 | 返回 |
|---|---|---|
| `/` | GET | 网页看板（HTML，1 秒自刷新） |
| `/state` | GET | 运行状态、帧率、当前目标、告警标志、运行时长 |
| `/events` | GET | 最近 40 条事件（JSON 数组，倒序） |
| `/trigger?sec=5` | GET | 外部触发一次告警并保持 5 秒（语音指令调用） |

事件格式（`events.jsonl`，一行一条）：

```json
{"type": "alert_start", "ts": "2026-11-06 19:16:02", "labels": ["person"], "zone": "patrol_area"}
{"type": "alert_end",   "ts": "2026-11-06 19:16:05", "duration_s": 3.5, "labels": ["person"]}
```

---

## 8. 实测数据

| 指标 | 值 | 条件 |
|---|---|---|
| 检测帧率 | 20~22 FPS（仅视觉）<br>14~19 FPS（视觉+语音同时跑） | 1280×720 MJPG，USB 摄像头 |
| 告警确认 | 8 帧（约 0.4 秒） | confirm_frames=8 |
| 告警解除 | 约 1 秒（含 0.8 秒看门狗超时） | release_frames=15 |
| 推理后端 | SpacemiT EP（NPU） | 日志 `Enable spacemit ep now` |
| 语音识别（ASR） | RTF 1.0~1.5 | SenseVoice，4 秒音频 → 约 4 秒推理 |
| 语音指令响应 | 约 8 秒 | 4 秒录音 + 4 秒推理（含轮询间隔） |

> 换板子、换机位、换光照都应重测，上面的数字仅对应上表环境。

---

## 9. 语音指令（第二模态）

`voice.py` 独立进程：循环录 4 秒 → 本地 SenseVoice 识别 → 命中关键词就调 `/trigger` 点亮告警。

```bash
sudo systemctl start cangyan-voice     # 已随代码安装为服务
journalctl -u cangyan-voice -f         # 看识别日志
```

**两个平台相关的坑：**

1. **录音必须走 PulseAudio，不能直接 `plughw`**
   音频设备被 pipewire 占着，`arecord -D plughw:2,0` 会报 `设备或资源忙`。
   正确写法（以桌面用户身份）：
   ```bash
   runuser -u dwb -- env XDG_RUNTIME_DIR=/run/user/1000 \
     arecord -q -D default -d 4 -f S16_LE -r 48000 -c 1 /tmp/x.wav
   ```
2. **ASR 必须以 root 跑**，模型在 `/root/.cache/sensevoice`。

**关键词匹配是"包含"而不是"全等"**：实测说"报警测试"时会识别成"报警册"，
因为词表里有 `报警`，所以照样命中——这是刻意留的容错。

---

## 10. 离线自测（不需要板子）

`idle_watchdog` 的行为无法靠肉眼在上板时反复验证，所以仓库里带了一个不依赖硬件的自测：

```bash
python3 tests/selftest.py
```

它会把 `monitor.py` 和「输出 3 秒后完全静默的假 demo」放进临时目录跑一遍，断言：

1. 目标持续进入禁区 → 连续 `confirm_frames` 帧后进入告警
2. demo 静默 → 看门狗补空帧 → 告警在 1~2 秒内解除
3. `events.jsonl` 中 `alert_start` / `alert_end` 成对，且带 `duration_s`

实测输出：

```
[1] 告警触发  t=0.76s  hit_streak=10
[2] 告警解除  t=4.4s  miss_streak=15
[3] 事件记录  ['alert_start', 'alert_end']
PASS
```

---

## 11. 已知限制

- 检测模型为官方预置 YOLOv6n（COCO 80 类），**未针对本场景重新训练**
- 无截图存证（事件只存文本）
- 无 GPIO 声光告警联动，告警呈现为网页红条
- ASR 比实时慢（RTF ≈ 1.0~1.5），现场说一句话约 8 秒后才有响应
- USB 总线带宽/供电紧张：摄像头 + 麦克风 + 键鼠同时插会掉设备，实测拔掉键鼠后稳定
- 语音与视觉是两个进程，通过 HTTP `/trigger` 联动，非进程内耦合

以上均为后续规划，不在本次提交的已完成范围内。

---

## 12. 许可

MIT，见 `LICENSE`。
