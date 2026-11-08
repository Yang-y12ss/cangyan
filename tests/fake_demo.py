"""假 demo：模拟 detection_stream_demo 的关键行为。

前 3 秒每 50ms 输出一帧（两行 bbox，模拟画面里有两个人）；
之后完全静默 8 秒（模拟人走出禁区后，demo 一行都不打印）。

这正是 monitor.py 的 idle_watchdog 要处理的情况。
"""
import time

t0 = time.time()
while time.time() - t0 < 3.0:
    for i in range(2):
        print(f"bbox[{i:2d}] x1y1x2y2: ( 200, 100, 400, 460), score: 0.87, label_text: person", flush=True)
    time.sleep(0.05)
time.sleep(8.0)
