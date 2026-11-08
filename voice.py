#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""仓眼 · 语音指令（第二模态）

循环录音 → 本地 SenseVoice 识别 → 命中关键词就调看板接口触发一次告警。
全程本地推理，不联网。

必须用 root 跑（ASR 模型在 /root/.cache/sensevoice）。
录音走 PulseAudio（dwb 的会话），所以内部用 runuser 切到 dwb 调 arecord。

用法：
    python3 voice.py            # 一直循环
    python3 voice.py --once     # 只跑一轮（测试用）
"""
import os
import subprocess
import sys
import time
import wave
import urllib.request

import numpy as np

sys.path.insert(0, "/home/dwb/spacemit-demo/examples/NLP")

RAW = "/tmp/voice_raw.wav"
P16 = "/tmp/voice_16k.wav"
TRIGGER = "http://127.0.0.1:8080/trigger?sec=5"

REC_SEC = 4          # 每轮录音时长
# 采样率：默认 48k（这颗 USB 麦在 48k 下识别准确率明显更好，实测「报警测试」一字不差）。
# 摄像头和麦克风同时插时可用环境变量 MIC_RATE=16000 降带宽，代价是识别率下降。
REC_RATE = int(os.environ.get("MIC_RATE", "48000"))
RMS_GATE = 250       # 低于此值视为静音，跳过推理（省 CPU）
KEYWORDS = ["报警测试", "报警", "警报测试", "测试"]


def record(sec=REC_SEC):
    """用 dwb 的 PulseAudio 会话录音。必须 -D default，直接 plughw 会被 pipewire 占用。"""
    subprocess.run(
        ["runuser", "-u", "dwb", "--",
         "env", "XDG_RUNTIME_DIR=/run/user/1000",
         "arecord", "-q", "-D", "default", "-d", str(sec),
         "-f", "S16_LE", "-r", str(REC_RATE), "-c", "1", RAW],
        check=True, timeout=sec + 10,
    )


def to16k(src=RAW):
    """转成 SenseVoice 要的 16k：48k 时 3:1 取平均，16k 时原样，再做峰值归一化。"""
    w = wave.open(src)
    d = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype("f4")
    w.close()

    if REC_RATE != 16000:
        m = (len(d) // 3) * 3
        d = d[:m].reshape(-1, 3).mean(axis=1)

    rms = float(np.sqrt((d.astype("f8") ** 2).mean()))
    peak = float(np.abs(d).max())
    if peak > 0:
        d = d * min(6.0, 28000.0 / peak)
    d = np.clip(d, -32768, 32767).astype("int16")
    o = wave.open(P16, "wb")
    o.setnchannels(1)
    o.setsampwidth(2)
    o.setframerate(16000)
    o.writeframes(d.tobytes())
    o.close()
    return rms, peak


def fire():
    try:
        with urllib.request.urlopen(TRIGGER, timeout=5) as r:
            return r.read().decode()
    except Exception as e:
        return "触发失败: %s" % e


def main():
    once = "--once" in sys.argv
    from spacemit_asr import ASRModel
    print("[voice] 加载 ASR 模型……", flush=True)
    model = ASRModel()
    print("[voice] 就绪，关键词：%s" % "、".join(KEYWORDS), flush=True)

    while True:
        t0 = time.time()
        try:
            record()
            rms, peak = to16k()
        except Exception as e:
            print("[voice] 录音失败: %s" % e, flush=True)
            time.sleep(2)
            continue

        if rms < RMS_GATE:
            print("[voice] %s 静音(RMS=%.0f)，跳过"
                  % (time.strftime("%H:%M:%S"), rms), flush=True)
        else:
            try:
                text = model.generate(P16)
            except Exception as e:
                text = ""
                print("[voice] 识别失败: %s" % e, flush=True)
            cost = time.time() - t0
            hit = [k for k in KEYWORDS if k in (text or "")]
            print("[voice] %s 峰值=%.0f RMS=%.0f 耗时=%.1fs 识别=%r%s"
                  % (time.strftime("%H:%M:%S"), peak, rms, cost, text,
                     "  ← 命中 " + hit[0] if hit else ""), flush=True)
            if hit:
                print("[voice] 触发告警 ->", fire(), flush=True)

        if once:
            break


if __name__ == "__main__":
    main()
