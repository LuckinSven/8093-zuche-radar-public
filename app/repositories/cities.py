from datetime import datetime

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.models import City


class CityRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_zuche_id(self, zuche_city_id: str) -> City | None:
        return self.session.scalar(select(City).where(City.zuche_city_id == zuche_city_id))

    def apply_catalog_snapshot(self, cities: list[dict], synced_at: datetime) -> dict[str, int]:
        """在一个短事务中，将已经校验完成的匿名目录写入数据库。"""
        ids = [city["zuche_city_id"] for city in cities]
        existing_ids = set(self.session.scalars(select(City.zuche_city_id).where(
            City.zuche_city_id.in_(ids))))
        rows = [{**city, "enabled": _is_guangzhou(city), "catalog_active": True,
                 "first_seen_at": synced_at, "last_seen_at": synced_at,
                 "catalog_synced_at": synced_at} for city in cities]
        statement = _insert_for_session(self.session, rows)
        self.session.execute(statement.on_conflict_do_update(
            index_elements=[City.zuche_city_id],
            set_={"name": statement.excluded.name,
                  "latitude": statement.excluded.latitude,
                  "longitude": statement.excluded.longitude,
                  "code": statement.excluded.code,
                  "en_name": statement.excluded.en_name,
                  "catalog_active": True,
                  "last_seen_at": statement.excluded.last_seen_at,
                  "catalog_synced_at": statement.excluded.catalog_synced_at},
        ))
        inactive_count = int(self.session.execute(
            update(City).where(City.catalog_active.is_(True), City.zuche_city_id.not_in(ids)).values(
                catalog_active=False, catalog_synced_at=synced_at)
        ).rowcount or 0)
        return {"city_count": len(cities), "created_count": len(ids) - len(existing_ids),
                "updated_count": len(existing_ids), "inactive_count": inactive_count}

    def acquire_catalog_sync_lock(self) -> None:
        if self.session.bind.dialect.name == "postgresql":
            self.session.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"),
                                 {"lock_id": 824739102})

    def stats(self) -> dict[str, int | str | None]:
        city_count = int(self.session.scalar(select(func.count()).select_from(City)) or 0)
        active_count = int(self.session.scalar(select(func.count()).select_from(City).where(
            City.catalog_active.is_(True))) or 0)
        last_synced_at = self.session.scalar(select(func.max(City.catalog_synced_at)))
        return {"city_count": city_count, "active_count": active_count,
                "last_synced_at": last_synced_at.isoformat() if last_synced_at else None}


def _is_guangzhou(city: dict) -> bool:
    return city["zuche_city_id"] == "14"


def _insert_for_session(session: Session, rows: list[dict]):
    if session.bind.dialect.name == "postgresql":
        return postgres_insert(City).values(rows)
    return sqlite_insert(City).values(rows)
