#!/usr/bin/env python3
"""扫局域网找 K1 板子：并发探测 22(SSH) / 8080(看板) 端口。

用法：
    python scan_lan.py              # 扫本机所在 /24 网段
    python scan_lan.py 192.168.1    # 指定网段前缀
"""
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor


def local_prefix():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    finally:
        s.close()
    return ip.rsplit(".", 1)[0], ip


def probe(ip, port, timeout=0.35):
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((ip, port))
        return ip, port
    except Exception:
        return None
    finally:
        s.close()


def banner(ip, port=22, timeout=1.5):
    try:
        s = socket.create_connection((ip, port), timeout=timeout)
        s.settimeout(timeout)
        data = s.recv(128)
        s.close()
        return data.decode("utf-8", "replace").strip()
    except Exception:
        return ""


def main():
    if len(sys.argv) > 1:
        prefix = sys.argv[1]
        me = "?"
    else:
        prefix, me = local_prefix()

    print(f"本机 IP: {me}")
    print(f"扫描网段: {prefix}.1-254 (端口 22 / 8080)")
    t0 = time.time()

    jobs = [(f"{prefix}.{i}", p) for i in range(1, 255) for p in (22, 8080)]
    hits = []
    with ThreadPoolExecutor(max_workers=256) as ex:
        for r in ex.map(lambda a: probe(*a), jobs):
            if r:
                hits.append(r)

    ssh_hosts = sorted({ip for ip, port in hits if port == 22}, key=lambda x: int(x.split(".")[-1]))
    web_hosts = sorted({ip for ip, port in hits if port == 8080}, key=lambda x: int(x.split(".")[-1]))

    print(f"\n耗时 {time.time()-t0:.1f}s")
    print(f"开放 22 的主机：{ssh_hosts or '无'}")
    print(f"开放 8080 的主机：{web_hosts or '无'}")

    for ip in ssh_hosts:
        b = banner(ip)
        tag = ""
        if "bianbu" in b.lower() or "ubuntu" in b.lower():
            tag = "  ← 很像 Bianbu/Ubuntu"
        print(f"\n  {ip}")
        print(f"    banner: {b[:80]}{tag}")

    if web_hosts:
        print("\n8080 上很可能就是仓眼看板：", ", ".join(f"http://{h}:8080" for h in web_hosts))
    if not ssh_hosts:
        print("\n没扫到 22 端口。检查：①板子是否已上电 ②网线是否插在同一路由器 ③是否等了 60 秒")


if __name__ == "__main__":
    main()
