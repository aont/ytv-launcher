import asyncio
import json
import os
import re
from urllib.parse import urlparse

from aiohttp import web

STATIC_DIR = "docs"
CORS_ALLOWED_ORIGINS = os.environ.get("CORS_ALLOW_ORIGINS", "*")


def get_cors_allow_origin(origin: str | None) -> str | None:
    if CORS_ALLOWED_ORIGINS == "*":
        return "*"
    if not origin:
        return None
    allowed = {item.strip() for item in CORS_ALLOWED_ORIGINS.split(",") if item.strip()}
    return origin if origin in allowed else None


def apply_cors_headers(request: web.Request, response: web.StreamResponse) -> None:
    allow_origin = get_cors_allow_origin(request.headers.get("Origin"))
    if allow_origin:
        response.headers["Access-Control-Allow-Origin"] = allow_origin
        response.headers["Vary"] = "Origin"
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"

def is_valid_youtube_url(url: str) -> bool:
    """
    Add allowed domains as needed.
    """
    try:
        u = urlparse(url)
    except Exception:
        return False

    if u.scheme not in ("http", "https"):
        return False

    host = (u.netloc or "").lower()

    # Allow youtube / youtu.be
    allowed_hosts = {
        "www.youtube.com",
        "youtube.com",
        "m.youtube.com",
        "youtu.be",
        "www.youtu.be",
    }
    if host not in allowed_hosts:
        return False

    # Roughly check the path as well (optional)
    # e.g. youtu.be/<id> or youtube.com/watch?v=...
    if host in ("youtu.be", "www.youtu.be"):
        return bool(u.path and u.path != "/")
    if host in ("youtube.com", "www.youtube.com", "m.youtube.com"):
        return u.path.startswith(("/watch", "/shorts", "/live", "/embed"))
    return False


async def send(ws: web.WebSocketResponse, typ: str, message: str, **extra):
    payload = {"type": typ, "message": message, **extra}
    await ws.send_str(json.dumps(payload, ensure_ascii=False))


async def stream_process_output(ws: web.WebSocketResponse, stream: asyncio.StreamReader, prefix: str):
    """
    Read subprocess stdout/stderr line by line and stream it to the frontend.
    """
    while True:
        line = await stream.readline()
        if not line:
            break
        text = line.decode(errors="replace").rstrip("\n")
        await send(ws, "log", f"[{prefix}] {text}")

import shlex
async def run_adb_intent(ws: web.WebSocketResponse, url: str):
    """
    Run adb with shell=False and stream logs to the frontend in real time.
    """
    # ★ Important: Keep shell=False → use create_subprocess_exec
    cmd = (
        "adb",
        "exec-out",
        shlex.join((
            "am",
            "start",
            "-a",
            "android.intent.action.VIEW",
            "-d",
            url,
            "-p",
            "com.google.android.youtube.tv",
        ))
    )

    await send(ws, "log", f"Command executed: {json.dumps(cmd)}")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        await send(ws, "error", "adb was not found. Please check your PATH.")
        return
    except Exception as e:
        await send(ws, "error", f"Failed to start process: {e!r}")
        return

    # Forward stdout/stderr concurrently
    assert proc.stdout is not None
    assert proc.stderr is not None
    t_out = asyncio.create_task(stream_process_output(ws, proc.stdout, "stdout"))
    t_err = asyncio.create_task(stream_process_output(ws, proc.stderr, "stderr"))

    rc = await proc.wait()
    await t_out
    await t_err

    await send(ws, "done", f"Exit code: {rc}", returncode=rc)


async def websocket_handler(request: web.Request):
    ws = web.WebSocketResponse(heartbeat=30)
    apply_cors_headers(request, ws)
    await ws.prepare(request)

    await send(ws, "log", "WebSocket connected. Please send a YouTube URL.")

    async for msg in ws:
        if msg.type == web.WSMsgType.TEXT:
            try:
                data = json.loads(msg.data)
            except json.JSONDecodeError:
                await send(ws, "error", "Please send data in JSON format. Example: {\"type\":\"open\",\"url\":\"...\"}")
                continue

            typ = data.get("type")
            if typ == "open":
                url = (data.get("url") or "").strip()
                await send(ws, "log", f"Received URL: {url}")

                if not is_valid_youtube_url(url):
                    await send(ws, "error", "The URL is invalid as a YouTube URL (check allowed domains/formats).")
                    continue

                await run_adb_intent(ws, url)

            elif typ == "ping":
                await send(ws, "pong", "pong")
            else:
                await send(ws, "error", f"Unknown type: {typ!r}")

        elif msg.type == web.WSMsgType.ERROR:
            # Connection error
            break

    return ws


def create_app() -> web.Application:
    @web.middleware
    async def cors_middleware(request: web.Request, handler):
        if request.method == "OPTIONS":
            response = web.Response(status=204)
        else:
            response = await handler(request)
        apply_cors_headers(request, response)
        return response

    app = web.Application(middlewares=[cors_middleware])

    # WebSocket
    app.router.add_get("/ws", websocket_handler)

    # Static file serving (index.html)
    app.router.add_static("/", path=STATIC_DIR, show_index=True)

    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=8080)
