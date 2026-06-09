from __future__ import annotations
import time
from fastapi import APIRouter, Depends
from app.auth import login_required
from app.settings import get_settings

router = APIRouter(prefix="/api")


@router.get("/cache/stats", dependencies=[Depends(login_required)])
async def cache_stats():
    import redis.asyncio as redis
    s = get_settings()
    host = s.redis_host or "valkey"
    port = int(s.redis_port or 6379)
    backend = f"{host}:{port}"
    try:
        r = redis.Redis(host=host, port=port, socket_connect_timeout=2, socket_timeout=2)
        t0 = time.perf_counter()
        await r.ping()
        rtt = (time.perf_counter() - t0) * 1000
        info = await r.info()
        hits = info.get("keyspace_hits", 0) or 0
        misses = info.get("keyspace_misses", 0) or 0
        total = hits + misses
        db_keys = sum(v.get("keys", 0) for k, v in info.items()
                      if isinstance(k, str) and k.startswith("db") and isinstance(v, dict))
        return {
            "connected": True, "backend": backend, "rtt_ms": round(rtt, 2), "type": "redis",
            "used_memory": info.get("used_memory"), "used_memory_human": info.get("used_memory_human"),
            "used_memory_peak_human": info.get("used_memory_peak_human"),
            "keyspace_hits": hits, "keyspace_misses": misses,
            "hit_rate": (hits / total) if total else None,
            "evicted_keys": info.get("evicted_keys", 0), "db_keys": db_keys,
            "connected_clients": info.get("connected_clients"),
            "uptime_in_seconds": info.get("uptime_in_seconds"),
        }
    except Exception as e:
        return {"connected": False, "backend": backend, "error": str(e)}
    else:
        try:
            await r.aclose()
        except Exception:
            pass


@router.get("/proxy-info", dependencies=[Depends(login_required)])
async def proxy_info():
    s = get_settings()
    return {"proxy_port": s.litellm_proxy_port, "proxy_host": s.litellm_proxy_host or None}
