"""本地 HTTP 代理桥：把 Chrome 的 HTTP CONNECT 转成上游 SOCKS5 隧道。

为什么需要：
  Chrome 直接 `--proxy-server=socks5://gateway:port` 时，隧道抖动会直接报
  ERR_TIMED_OUT / ERR_NETWORK_CHANGED（实测）。本地起一个桥，Chrome 只跟
  127.0.0.1 说话（瞬间返回），隧道问题由桥这一层处理，并且可以带重试。

不改 TLS（纯隧道），所以 Chrome 不需要忽略证书错误。

用法:
    python3 tools/socks_bridge.py --socks 146.190.52.23:8536 --listen 127.0.0.1:18080
    # 然后 Chrome: --proxy-server=http://127.0.0.1:18080
"""
from __future__ import annotations

import argparse
import asyncio
import sys

try:
    import socks  # PySocks
except ImportError:
    sys.exit("需要 pysocks：pip install pysocks")


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                 up_host: str, up_port: int, retries: int = 2) -> None:
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=20)
    except Exception:
        writer.close()
        return

    try:
        first = head.split(b"\r\n", 1)[0].decode("latin1")
    except Exception:
        writer.close()
        return

    if not first.upper().startswith("CONNECT "):
        # 非 CONNECT（明文 http）也照隧道转发，简单处理
        target = first.split(" ")[1] if " " in first else ""
    else:
        target = first.split(" ")[1]

    if ":" in target:
        host, port_s = target.rsplit(":", 1)
        try:
            port = int(port_s)
        except ValueError:
            port = 443
    else:
        host, port = target, 443

    upstream = None
    last_err = None
    for _ in range(retries + 1):
        try:
            upstream = await asyncio.get_event_loop().run_in_executor(
                None, lambda: socks.create_connection(
                    (host, port), timeout=25, proxy_type=socks.SOCKS5,
                    proxy_addr=up_host, proxy_port=up_port))
            break
        except Exception as e:
            last_err = e
            await asyncio.sleep(0.8)
    if upstream is None:
        writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
        await writer.drain()
        writer.close()
        return

    if first.upper().startswith("CONNECT "):
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
    else:
        upstream.sendall(head)          # 明文请求直接透传

    up_r, up_w = await asyncio.open_connection(sock=upstream)

    async def pump(r, w):
        try:
            while True:
                data = await r.read(65536)
                if not data:
                    break
                w.write(data)
                await w.drain()
        except Exception:
            pass
        finally:
            try:
                w.close()
            except Exception:
                pass

    await asyncio.gather(pump(reader, up_w), pump(up_r, writer))


async def main_async(listen_host: str, listen_port: int, up_host: str, up_port: int) -> None:
    server = await asyncio.start_server(
        lambda r, w: handle(r, w, up_host, up_port), listen_host, listen_port)
    print(f"[bridge] http://{listen_host}:{listen_port}  ->  socks5://{up_host}:{up_port}", flush=True)
    async with server:
        await server.serve_forever()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--socks", required=True, help="上游 socks5 host:port")
    ap.add_argument("--listen", default="127.0.0.1:18080")
    a = ap.parse_args()
    uh, up = a.socks.split(":")
    lh, lp = a.listen.split(":")
    try:
        asyncio.run(main_async(lh, int(lp), uh, int(up)))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
