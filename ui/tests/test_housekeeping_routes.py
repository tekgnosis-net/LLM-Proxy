import os, pytest
from fastapi.testclient import TestClient
from app.auth import hash_password


def _client(tmp_path, fake):
    os.environ.update(ADMIN_PASSWORD_HASH=hash_password("pw"), SESSION_SECRET="s",
                      CONFIG_PATH=str(tmp_path / "c.yaml"), DATABASE_URL="postgresql://x")
    (tmp_path / "c.yaml").write_text("model_list: []\n")
    from app.main import create_app
    import app.routes.housekeeping_routes as hk
    hk.make_db_admin = lambda: fake
    c = TestClient(create_app()); c.post("/api/auth/login", json={"password": "pw"}); return c


class FakeDb:
    def __init__(self): self.ran = None
    async def stats(self): return {"row_counts": {"LiteLLM_SpendLogs": 5}, "db_size": "12 MB"}
    async def run_maintenance(self, retention_days, delete_expired_keys=True):
        self.ran = (retention_days, delete_expired_keys); return {"trimmed_spend_logs": 3, "deleted_expired_keys": 1, "retention_days": retention_days}


def test_housekeeping_requires_login(tmp_path):
    c = _client(tmp_path, FakeDb()); c.cookies.clear()
    assert c.get("/api/housekeeping").status_code == 401


def test_housekeeping_stats(tmp_path):
    d = _client(tmp_path, FakeDb()).get("/api/housekeeping").json()
    assert d["stats"]["db_size"] == "12 MB"
    assert d["settings"]["retention_days"] == 90 and d["settings"]["enabled"] is False


def test_housekeeping_run(tmp_path):
    fake = FakeDb(); c = _client(tmp_path, fake)
    d = c.post("/api/housekeeping/run").json()
    assert d["trimmed_spend_logs"] == 3 and fake.ran == (90, True)
