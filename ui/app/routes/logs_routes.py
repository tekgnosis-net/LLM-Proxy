import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from app.auth import login_required
from app.settings import get_settings

router = APIRouter(prefix="/api")


def deframe_docker_log(buf: bytes):
    """Parse complete Docker multiplexed-log frames from buf.
    Each frame: 8-byte header [stream_type, 0,0,0, size(4B big-endian)] + size payload bytes.
    Returns (payloads:list[str], remaining:bytes) — remaining holds an incomplete trailing frame."""
    out, i, n = [], 0, len(buf)
    while n - i >= 8:
        size = int.from_bytes(buf[i + 4:i + 8], "big")
        if n - i - 8 < size:
            break
        out.append(buf[i + 8:i + 8 + size].decode("utf-8", "replace"))
        i += 8 + size
    return out, buf[i:]


async def _log_events(tail: int):
    s = get_settings()
    url = f"{s.socket_proxy_url.rstrip('/')}/containers/{s.litellm_container}/logs"
    params = {"follow": "1", "stdout": "1", "stderr": "1", "timestamps": "1", "tail": str(tail)}
    buf = b""
    try:
        async with httpx.AsyncClient(timeout=None) as c:
            async with c.stream("GET", url, params=params) as r:
                if r.status_code >= 400:
                    yield f"data: [log stream unavailable: HTTP {r.status_code}]\n\n"
                    return
                async for chunk in r.aiter_bytes():
                    buf += chunk
                    lines, buf = deframe_docker_log(buf)
                    for ln in lines:
                        for one in ln.rstrip("\n").split("\n"):
                            yield f"data: {one}\n\n"
    except (httpx.HTTPError, Exception) as e:   # client disconnect / upstream drop
        yield f"data: [log stream closed: {type(e).__name__}]\n\n"


@router.get("/logs/stream", dependencies=[Depends(login_required)])
async def logs_stream(tail: int = 200):
    tail = max(1, min(int(tail), 1000))
    return StreamingResponse(_log_events(tail), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
