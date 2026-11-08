#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
仓眼 —— 基于 K1 的边缘视觉监测终端（上层应用）
读取官方检测程序的输出，做禁区判定 / 连续帧防抖 / 事件留痕 / 网页看板

零第三方依赖，只用标准库。
"""
import os
import re
import json
import time
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(BASE, "config.json")
EVENT_PATH = os.path.join(BASE, "events.jsonl")

# ---------- 配置 ----------
DEFAULT_CFG = {
    "demo_bin": "/usr/bin/detection_stream_demo",
    "model": "/usr/share/ai-support/models/yolov6.json",
    "camera": "/dev/video20",
    # ⚠️ 必须带 -f：关掉 demo 自带的预览窗口。
    # 开着预览时，预览窗口和检测线程会各自打开一次摄像头，
    # 第二个 open 会把第一个的视频流顶掉（VIDIOC_REQBUFS: errno=19），
    # 表现为窗口闪一下就退出。实测加 -f 后可连续出流 12 秒零错误。
    "demo_extra_args": ["-f"],
    "frame_w": 640,
    "frame_h": 480,
    # 禁区多边形（归一化 0~1 坐标）。默认整幅画面，现场按需要改
    "zone": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
    "confirm_frames": 8,      # 连续多少帧命中才确认告警（与板上实测配置一致）
    "release_frames": 15,     # 连续多少帧未命中才解除
    "watch_classes": ["person"],  # 空 = 关注所有类别；本项目只看人
    "http_port": 8080,
    "min_score": 0.40,
    # 看门狗：超过这么多秒没有任何 bbox 帧，就按"本帧无目标"处理。
    # 必须项——demo 在画面无目标时不打印任何 bbox 行，
    # 不加这条，人走出禁区后 miss_streak 永不增长，告警永不解除。
    "idle_timeout_s": 0.8,
    # demo 意外退出后多久重连（开机时摄像头枚举慢、USB 抖动都靠它兜底）
    "restart_delay_s": 5
}


def load_cfg():
    cfg = dict(DEFAULT_CFG)
    if os.path.exists(CFG_PATH):
        try:
            with open(CFG_PATH, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception as e:
            print("[cfg] 读取失败，使用默认配置:", e)
    return cfg


CFG = load_cfg()

BBOX_RE = re.compile(
    r"bbox\[\s*\d+\]\s*x1y1x2y2:\s*\(\s*(-?\d+),\s*(-?\d+),\s*(-?\d+),\s*(-?\d+)\)"
    r"\s*,\s*score:\s*([0-9.]+)\s*,\s*label_text:\s*(.+?)\s*$"
)

# ---------- 全局状态 ----------
LOCK = threading.Lock()
STATE = {
    "running": False,
    "fps": 0.0,
    "frames": 0,
    "objects": [],       # 当前帧检测结果
    "hit": False,        # 当前帧是否有目标落在禁区
    "hit_streak": 0,
    "miss_streak": 0,
    "alert": False,
    "alert_since": None,
    "alert_count": 0,
    "last_labels": [],
    "uptime": 0,
    "last_frame_ago": None,   # 距上一帧检测输出的秒数（看门狗用）
    "voice_until": 0.0,       # 语音触发的告警保持到这个时间点之前不自动解除
    "zone": CFG["zone"],
    "camera": CFG["camera"],
}
START_TS = time.time()
ALERT_EVENT = {"open": False, "start": None, "labels": set()}
LAST_FRAME_TS = 0.0      # 最近一次"完整帧"的时间戳（含看门狗补帧）


def point_in_poly(x, y, poly):
    """射线法判断点是否在多边形内（poly 为归一化坐标）"""
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def write_event(evt):
    try:
        with open(EVENT_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(evt, ensure_ascii=False) + "\n")
    except Exception as e:
        print("[event] 写盘失败:", e)


def process_frame(boxes):
    """一帧结束时的判定逻辑"""
    global LAST_FRAME_TS
    LAST_FRAME_TS = time.time()
    cfg = CFG
    hit = False
    labels = []
    for (x1, y1, x2, y2, score, label) in boxes:
        if score < cfg["min_score"]:
            continue
        if cfg["watch_classes"] and label not in cfg["watch_classes"]:
            continue
        cx = (x1 + x2) / 2.0 / cfg["frame_w"]
        cy = (y1 + y2) / 2.0 / cfg["frame_h"]
        labels.append(label)
        if point_in_poly(cx, cy, cfg["zone"]):
            hit = True

    with LOCK:
        STATE["objects"] = [{"label": l, "score": s} for (*_, s, l) in boxes
                            if s >= cfg["min_score"]
                            and (not cfg["watch_classes"] or l in cfg["watch_classes"])][:8]
        STATE["last_labels"] = labels

        if hit:
            STATE["hit"] = True
            STATE["hit_streak"] = STATE.get("hit_streak", 0) + 1
            STATE["miss_streak"] = 0
        else:
            STATE["hit"] = False
            STATE["miss_streak"] = STATE.get("miss_streak", 0) + 1
            STATE["hit_streak"] = 0

        # 进入告警
        if not STATE["alert"] and STATE.get("hit_streak", 0) >= cfg["confirm_frames"]:
            STATE["alert"] = True
            STATE["alert_since"] = time.time()
            STATE["alert_count"] += 1
            ALERT_EVENT["open"] = True
            ALERT_EVENT["start"] = time.time()
            ALERT_EVENT["labels"] = set(labels)
            evt = {
                "type": "alert_start",
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "labels": sorted(set(labels)),
                "zone": "patrol_area",
            }
            write_event(evt)
            print("[ALERT] 进入禁区:", labels)

        # 解除告警（语音触发期间保持，不被普通帧判据抢掉）
        if (STATE["alert"] and STATE.get("miss_streak", 0) >= cfg["release_frames"]
                and time.time() >= STATE.get("voice_until", 0)):
            dur = round(time.time() - (ALERT_EVENT["start"] or time.time()), 1)
            STATE["alert"] = False
            STATE["alert_since"] = None
            ALERT_EVENT["open"] = False
            evt = {
                "type": "alert_end",
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "duration_s": dur,
                "labels": sorted(ALERT_EVENT["labels"]),
            }
            write_event(evt)
            print("[ALERT] 解除，持续 %.1fs" % dur)

        if STATE["alert"] and ALERT_EVENT["open"]:
            ALERT_EVENT["labels"] |= set(labels)


def _run_demo_once():
    """跑一次 demo，直到它自己退出"""
    cmd = [CFG["demo_bin"]] + list(CFG.get("demo_extra_args") or []) + [CFG["model"], CFG["camera"]]
    print("[demo] 启动:", " ".join(cmd))
    env = dict(os.environ)
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         bufsize=1, universal_newlines=True, env=env)
    boxes = []
    fps_t0 = time.time()
    fps_n = 0
    with LOCK:
        STATE["running"] = True
    for line in p.stdout:
        m = BBOX_RE.search(line)
        if m:
            x1, y1, x2, y2 = (max(0, int(m.group(i))) for i in range(1, 5))
            score = float(m.group(5))
            label = m.group(6)
            if line.lstrip().startswith("bbox[ 0]"):
                if boxes:
                    process_frame(boxes)
                    fps_n += 1
                boxes = [(x1, y1, x2, y2, score, label)]
            else:
                boxes.append((x1, y1, x2, y2, score, label))
            # 每秒更新一次帧率
            now = time.time()
            if now - fps_t0 >= 1.0:
                with LOCK:
                    STATE["fps"] = round(fps_n / (now - fps_t0), 1)
                    STATE["frames"] += fps_n
                fps_t0, fps_n = now, 0
        elif line.strip() and not line.lstrip().startswith("bbox"):
            print("[demo]", line.rstrip()[:160])
    p.wait()
    with LOCK:
        STATE["running"] = False
    print("[demo] 进程退出，返回码", p.returncode)


def reader_thread():
    """demo 意外退出时自动重连。

    两个真实场景靠它兜底：
    1. 开机时服务比摄像头枚举更快 → demo 打不开设备直接退出
    2. USB 抖动导致摄像头掉线 → 传输中断，demo 退出
    没有这个循环，一旦退出就永远显示"已停止"，只能人工重启。
    """
    delay = CFG.get("restart_delay_s", 5)
    while True:
        try:
            _run_demo_once()
        except Exception as e:
            print("[demo] 启动异常:", e)
        with LOCK:
            STATE["running"] = False
        print("[demo] %s 秒后重连……" % delay)
        time.sleep(delay)


def idle_watchdog():
    """看门狗。

    detection_stream_demo 只在有目标时才打印 bbox 行；画面里没人时一行都不输出，
    于是 process_frame 不会被调用，miss_streak 永远不增长 —— 人走出禁区后告警不会解除。
    这里在"超过 idle_timeout_s 没有新的完整帧"时补一个空帧，
    让未命中计数正常累加；若当时正在告警，一次性补满 release_frames 帧，
    保证红条在约 1 秒内消失，而不是拖 7~8 秒。
    """
    while True:
        time.sleep(0.2)
        if not STATE["running"]:
            continue
        ts = LAST_FRAME_TS
        if ts:
            ago = time.time() - ts
            if ago <= CFG["idle_timeout_s"]:
                with LOCK:
                    STATE["last_frame_ago"] = round(ago, 2)
                continue
        else:
            continue
        try:
            if STATE["alert"] and time.time() >= STATE.get("voice_until", 0):
                for _ in range(CFG["release_frames"]):
                    process_frame([])
                print("[watchdog] 长时间无目标，补确认解除")
            else:
                process_frame([])
        except Exception as e:
            print("[watchdog] 补帧失败:", e)


PAGE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>仓眼 · 边缘视觉监测终端</title>
<style>
 body{margin:0;font-family:-apple-system,"Microsoft YaHei",sans-serif;background:#0f1115;color:#e6e6e6}
 header{padding:14px 20px;display:flex;align-items:center;gap:14px}
 .dot{width:12px;height:12px;border-radius:50%;background:#3ddc84;box-shadow:0 0 10px #3ddc84}
 h1{font-size:18px;margin:0;font-weight:600}
 .alert header .dot{background:#ff4d4f;box-shadow:0 0 16px #ff4d4f}
 .banner{display:none;background:#ff4d4f;color:#fff;padding:10px 20px;font-weight:700;letter-spacing:1px}
 .alert .banner{display:block}
 main{display:grid;grid-template-columns:1fr 1fr;gap:16px;padding:0 20px 20px}
 .card{background:#171a21;border:1px solid #232833;border-radius:10px;padding:14px 16px}
 .k{color:#8b93a7;font-size:12px}
 .v{font-size:26px;font-weight:700;margin-top:4px}
 .row{display:flex;gap:24px;flex-wrap:wrap}
 ul{list-style:none;padding:0;margin:8px 0 0;max-height:300px;overflow:auto}
 li{padding:8px 0;border-bottom:1px solid #232833;font-size:13px;display:flex;gap:10px}
 .tag{background:#252b38;border-radius:4px;padding:1px 6px;color:#9fb0ff}
 .muted{color:#6b7280}
</style>
</head>
<body>
<div class="banner">⚠ 检测到目标进入禁区</div>
<header><div class="dot"></div><h1>仓眼 · 边缘视觉监测终端</h1><span class="muted" id="cam"></span></header>
<main>
  <div class="card">
    <div class="row">
      <div><div class="k">状态</div><div class="v" id="status">--</div></div>
      <div><div class="k">帧率</div><div class="v" id="fps">--</div></div>
      <div><div class="k">告警次数</div><div class="v" id="cnt">--</div></div>
      <div><div class="k">运行时长</div><div class="v" id="up">--</div></div>
    </div>
  </div>
  <div class="card">
    <div class="k">当前检测目标</div>
    <ul id="objs"></ul>
  </div>
  <div class="card" style="grid-column:1/-1">
    <div class="k">事件记录</div>
    <ul id="evts"></ul>
  </div>
</main>
<script>
async function tick(){
  try{
    const s = await (await fetch('/state')).json();
    document.body.classList.toggle('alert', !!s.alert);
    document.getElementById('status').textContent =
      !s.running ? '已停止'
      : ((s.last_frame_ago !== null && s.last_frame_ago > 1.5) ? '运行中·无目标' : '运行中');
    document.getElementById('fps').textContent = s.fps ? s.fps.toFixed(1) : '--';
    document.getElementById('cnt').textContent = s.alert_count;
    document.getElementById('up').textContent = Math.floor(s.uptime/60)+' 分';
    document.getElementById('cam').textContent = s.camera;
    document.getElementById('objs').innerHTML = (s.objects||[]).map(o=>
      `<li><span class="tag">${o.label}</span><span class="muted">${o.score.toFixed(2)}</span></li>`).join('')
      || '<li class="muted">暂无目标</li>';
    const ev = await (await fetch('/events')).json();
    document.getElementById('evts').innerHTML = ev.map(e=>
      `<li><span class="muted">${e.ts}</span><span class="tag">${e.type==='alert_start'?'进入':'解除'}</span>
       <span>${(e.labels||[]).join(', ')}</span>
       <span class="muted">${e.duration_s?('持续 '+e.duration_s+'s'):''}</span></li>`).join('')
      || '<li class="muted">暂无事件</li>';
  }catch(e){ document.getElementById('status').textContent='连接断开'; }
}
tick(); setInterval(tick, 1000);
</script>
</body></html>"""


def trigger_alert(seconds=4.0, source="voice"):
    """外部触发一次告警（语音指令用）。

    在 seconds 秒内不会被普通帧判据解除，保证看板红条能稳定显示：
    否则画面里没人时 miss_streak 早已满足解除条件，红条刚亮就被清掉。
    """
    now = time.time()
    with LOCK:
        STATE["voice_until"] = now + seconds
        ALERT_EVENT["open"] = True
        ALERT_EVENT["start"] = now
        ALERT_EVENT["labels"] = {source}
        if not STATE["alert"]:
            STATE["alert"] = True
            STATE["alert_since"] = now
            STATE["alert_count"] += 1
    write_event({
        "type": "alert_start",
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "labels": [source],
        "zone": "voice_command",
    })
    print("[VOICE] 语音触发告警，保持 %.0f 秒" % seconds)

    def _release():
        time.sleep(seconds)
        with LOCK:
            if not (STATE["alert"] and time.time() >= STATE["voice_until"] - 0.05):
                return
            dur = round(time.time() - (ALERT_EVENT["start"] or time.time()), 1)
            STATE["alert"] = False
            STATE["alert_since"] = None
            ALERT_EVENT["open"] = False
        write_event({
            "type": "alert_end",
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "duration_s": dur,
            "labels": [source],
        })
        print("[VOICE] 语音告警解除，持续 %.1fs" % dur)

    threading.Thread(target=_release, daemon=True).start()
    return seconds


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/state"):
            with LOCK:
                s = dict(STATE)
                s["uptime"] = int(time.time() - START_TS)
            body = json.dumps(s, ensure_ascii=False).encode()
            ctype = "application/json; charset=utf-8"
        elif self.path.startswith("/events"):
            items = []
            try:
                with open(EVENT_PATH, "r", encoding="utf-8") as f:
                    lines = f.readlines()[-40:]
                items = [json.loads(x) for x in lines if x.strip()]
                items.reverse()
            except FileNotFoundError:
                pass
            body = json.dumps(items, ensure_ascii=False).encode()
            ctype = "application/json; charset=utf-8"
        elif self.path.startswith("/trigger"):
            m = re.search(r"sec=([0-9.]+)", self.path)
            secs = float(m.group(1)) if m else 4.0
            trigger_alert(secs)
            body = json.dumps({"ok": True, "hold_s": secs}).encode()
            ctype = "application/json; charset=utf-8"
        else:
            body = PAGE.encode()
            ctype = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    if not os.path.exists(CFG_PATH):
        with open(CFG_PATH, "w", encoding="utf-8") as f:
            json.dump(CFG, f, ensure_ascii=False, indent=2)
        print("[cfg] 已生成默认配置:", CFG_PATH)

    t = threading.Thread(target=reader_thread, daemon=True)
    t.start()

    w = threading.Thread(target=idle_watchdog, daemon=True)
    w.start()

    srv = ThreadingHTTPServer(("0.0.0.0", CFG["http_port"]), Handler)
    print("[web] http://0.0.0.0:%d" % CFG["http_port"])
    srv.serve_forever()


if __name__ == "__main__":
    main()
