import os, json, pytest
from pathlib import Path
from fastapi.testclient import TestClient
from app.auth import hash_password


class FakeEngine:
    def __init__(self, tmp):
        self.dir = Path(tmp); self.ran = []
        self.running = {"config": False, "logs": False}
    def backup_path(self, bid):
        from app.backup_engine import _ID_RE
        if not _ID_RE.match(bid): raise ValueError("bad id")
        return self.dir / bid
    async def run_config(self): self.ran.append("config"); return {"ok": True, "path": "config/x"}
    async def run_logs(self): self.ran.append("logs"); return {"ok": True, "path": "logs/x"}
    def list_backups(self): return {"backups": [{"id": "config/x", "tier": "config"}], "snapshots": []}


class FakeBStore:
    def __init__(self):
        from app.backup_store import DEFAULTS
        self.settings = {"config": dict(DEFAULTS["config"]), "logs": dict(DEFAULTS["logs"])}
        self.saved = []
    async def get_settings(self): return self.settings
    async def save_settings(self, tier, value): self.saved.append((tier, value))
    async def runs(self, tier=None, limit=20): return []
    async def last_run(self, tier, status="ok"): return None


class FakeModels:
    def __init__(self, n): self.n = n
    async def list_models(self):
        return [{"model_name": f"m{i}", "model_info": {"id": str(i)}} for i in range(self.n)]


class FakeCStore:
    def __init__(self, items): self.items = items
    async def applied(self): return self.items


def _client(tmp_path, engine=None, bstore=None, models=None, cstore=None):
    os.environ.update(ADMIN_PASSWORD_HASH=hash_password("pw"), SESSION_SECRET="s",
                      CONFIG_PATH=str(tmp_path / "c.yaml"), DATABASE_URL="postgresql://x",
                      BACKUP_DIR=str(tmp_path / "backups"))
    (tmp_path / "c.yaml").write_text("model_list: []\n")
    from app.main import create_app
    import app.routes.backup_routes as br
    br.make_backup_engine = lambda: engine or FakeEngine(tmp_path / "backups")
    br.make_backup_store = lambda: bstore or FakeBStore()
    br.make_models_client = lambda: models or FakeModels(0)
    br.make_config_store = lambda: cstore or FakeCStore([])
    c = TestClient(create_app()); c.post("/api/auth/login", json={"password": "pw"}); return c


def test_backup_routes_require_login(tmp_path):
    c = _client(tmp_path); c.cookies.clear()
    assert c.get("/api/backup/status").status_code == 401


def test_status_flags_empty_master_with_live_models(tmp_path):
    c = _client(tmp_path, models=FakeModels(3), cstore=FakeCStore([]))
    d = c.get("/api/backup/status").json()
    assert d["master_empty_live_nonempty"] is True and d["live_models"] == 3
    assert set(d["tiers"]) == {"config", "logs"}


def test_settings_put_validates_and_saves(tmp_path):
    bs = FakeBStore()
    c = _client(tmp_path, bstore=bs)
    r = c.put("/api/backup/settings", json={"config": {"enabled": True,
        "frequency": {"kind": "daily"}, "time": "02:00", "retention_days": 7}})
    assert r.status_code == 200 and bs.saved and bs.saved[0][0] == "config"
    r2 = c.put("/api/backup/settings", json={"config": {"frequency": {"kind": "hourly"}, "time": "02:00"}})
    assert r2.status_code == 422


def test_run_now_and_list(tmp_path):
    eng = FakeEngine(tmp_path / "b")
    c = _client(tmp_path, engine=eng)
    assert c.post("/api/backup/run", json={"tier": "logs"}).json()["ok"] is True
    assert eng.ran == ["logs"]
    assert c.post("/api/backup/run", json={"tier": "nope"}).status_code == 422
    assert c.get("/api/backup/list").json()["backups"][0]["id"] == "config/x"


def test_confirmation_strings_enforced(tmp_path):
    c = _client(tmp_path)
    assert c.post("/api/backup/rollback", json={"source": "snapshots/x.json", "confirm": "no"}).status_code == 422
    assert c.post("/api/backup/recover", json={"source": "config/x", "confirm": "no"}).status_code == 422
    assert c.post("/api/backup/restore-logs", json={"source": "all", "confirm": "no"}).status_code == 422


def test_download_rejects_traversal_and_missing(tmp_path):
    c = _client(tmp_path)
    assert c.get("/api/backup/download", params={"path": "config/../../etc/passwd"}).status_code == 422
    assert c.get("/api/backup/download", params={"path": "config/none/file"}).status_code == 404
