# ui/app/routes/backup_routes.py  (placeholder created in Task 5, completed in Task 9)
from fastapi import APIRouter
router = APIRouter(prefix="/api")


def make_backup_engine():
    from app.settings import get_settings
    from app.backup_engine import BackupEngine
    from app.backup_store import BackupStore
    from app.config_db import ConfigStore
    s = get_settings()
    return BackupEngine(s.database_url, s.backup_dir, BackupStore(s.database_url),
                        ConfigStore(s.database_url), s.config_path,
                        fernet_secret=(s.credentials_key or s.session_secret),
                        salt_key=None)
