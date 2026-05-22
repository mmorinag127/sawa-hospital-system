import json
from pathlib import Path

from src.db import session_scope
from src.services import config_service
from src.services import facility_service


def get_master() -> dict:
    config_service.reload_configs()
    base_master = config_service.load_facility_master()
    db_master = facility_service.build_facility_master_from_db(base_master)
    if db_master.get("facilities"):
        return db_master
    return base_master


def get_master_path() -> Path:
    return config_service.FACILITY_MASTER_PATH


def save_master(master: dict) -> None:
    with session_scope() as session:
        facility_service.upsert_facilities_and_configs_from_master(session, master)
    path = get_master_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(master, ensure_ascii=True, indent=2)
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    tmp_path.write_text(payload, encoding="utf-8")
    tmp_path.replace(path)
    config_service.reload_configs()
