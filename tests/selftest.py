#!/usr/bin/env python3
"""离线自测：不需要板子、不需要摄像头、不需要官方检测程序。

把 monitor.py 和一个"会静默的假 demo"放进临时目录跑一遍，验证状态机：

  1. 有目标持续出现在禁区 → 连续 confirm_frames 帧后进入告警
  2. 目标消失（demo 完全静默）→ 看门狗补空帧 → 告警在约 1~2 秒内解除
  3. events.jsonl 里 alert_start / alert_end 成对出现，且带 duration_s

用法：python3 tests/selftest.py
退出码：0 通过 / 1 失败
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PORT = 18080


def wait_state(timeout=15.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/state", timeout=2) as r:
                return json.loads(r.read().decode())
        except Exception:
            time.sleep(0.25)
    return None


def main():
    tmp = tempfile.mkdtemp(prefix="cangyan-selftest-")
    try:
        shutil.copy(os.path.join(ROOT, "monitor.py"), tmp)
        cfg = {
            "demo_bin": sys.executable,
            "model": os.path.join(HERE, "fake_demo.py").replace("\\", "/"),
            "camera": "selftest",
            "frame_w": 640,
            "frame_h": 480,
            "zone": [[0.25, 0.2], [0.75, 0.2], [0.75, 0.8], [0.25, 0.8]],
            "confirm_frames": 8,
            "release_frames": 15,
            "watch_classes": ["person"],
            "min_score": 0.45,
            "http_port": PORT,
            "idle_timeout_s": 0.8,
        }
        with open(os.path.join(tmp, "config.json"), "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)

        env = dict(os.environ, PYTHONUNBUFFERED="1")
        p = subprocess.Popen(
            [sys.executable, "-u", "monitor.py"], cwd=tmp, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            universal_newlines=True, encoding="utf-8", errors="replace",
        )

        s = wait_state()
        if s is None:
            print("FAIL: 看板端口没起来")
            return 1

        saw_alert = False
        cleared_at = None
        t0 = time.time()
        while time.time() - t0 < 14:
            s = wait_state(2)
            if not s:
                continue
            if s["alert"] and not saw_alert:
                saw_alert = True
                alert_at = round(time.time() - t0, 2)
                print(f"[1] 告警触发  t={alert_at}s  hit_streak={s['hit_streak']}")
            elif saw_alert and not s["alert"]:
                cleared_at = round(time.time() - t0, 2)
                print(f"[2] 告警解除  t={cleared_at}s  miss_streak={s['miss_streak']}")
                break
            time.sleep(0.25)

        p.terminate()
        try:
            p.wait(timeout=5)
        except Exception:
            p.kill()

        ok = True
        if not saw_alert:
            print("FAIL: 始终没有进入告警")
            ok = False
        if cleared_at is None:
            print("FAIL: 目标消失后告警没有解除（看门狗未生效）")
            ok = False

        ev = os.path.join(tmp, "events.jsonl")
        if not os.path.exists(ev):
            print("FAIL: 没有生成 events.jsonl")
            ok = False
        else:
            rows = [json.loads(x) for x in open(ev, encoding="utf-8") if x.strip()]
            kinds = [r["type"] for r in rows]
            print(f"[3] 事件记录  {kinds}")
            if "alert_start" not in kinds or "alert_end" not in kinds:
                print("FAIL: alert_start / alert_end 不成对")
                ok = False
            for r in rows:
                if r["type"] == "alert_end" and "duration_s" not in r:
                    print("FAIL: alert_end 缺 duration_s")
                    ok = False

        print("PASS" if ok else "FAIL")
        return 0 if ok else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
