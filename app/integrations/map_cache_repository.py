from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.orm import Session

from app.models import CoordinateCache, MapSearchCache


DEFAULT_PROVIDER = "baidu_maps"


def normalize_cache_text(value: str) -> str:
    return " ".join(value.strip().split()).casefold()


def normalize_coordinate(value: float) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)


class MapCacheRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_search(self, region: str, keyword: str,
                   provider: str = DEFAULT_PROVIDER) -> MapSearchCache | None:
        return self.session.scalar(select(MapSearchCache).where(
            MapSearchCache.provider == provider,
            MapSearchCache.normalized_region == normalize_cache_text(region),
            MapSearchCache.normalized_keyword == normalize_cache_text(keyword),
        ))

    def save_search(self, region: str, keyword: str, results: list,
                    provider: str = DEFAULT_PROVIDER) -> MapSearchCache:
        statement = postgresql_insert(MapSearchCache).values(
            provider=provider,
            region=region,
            keyword=keyword,
            normalized_region=normalize_cache_text(region),
            normalized_keyword=normalize_cache_text(keyword),
            results_json=results,
        )
        statement = statement.on_conflict_do_update(
            constraint="uq_map_search_cache_provider_region_keyword",
            set_={
                "region": statement.excluded.region,
                "keyword": statement.excluded.keyword,
                "results_json": statement.excluded.results_json,
                "refreshed_at": func.now(),
            },
        ).returning(MapSearchCache.id)
        item_id = self.session.scalar(statement)
        return self.session.get(MapSearchCache, item_id, populate_existing=True)

    def hit_search(self, item: MapSearchCache) -> MapSearchCache:
        self.session.execute(update(MapSearchCache).where(MapSearchCache.id == item.id).values(
            hit_count=MapSearchCache.hit_count + 1,
            last_hit_at=datetime.now(timezone.utc)))
        self.session.flush()
        self.session.refresh(item)
        return item

    def get_coordinate(self, source_crs: str, target_crs: str, source_latitude: float,
                       source_longitude: float,
                       provider: str = DEFAULT_PROVIDER) -> CoordinateCache | None:
        return self.session.scalar(select(CoordinateCache).where(
            CoordinateCache.provider == provider,
            CoordinateCache.source_crs == source_crs,
            CoordinateCache.target_crs == target_crs,
            CoordinateCache.source_latitude == normalize_coordinate(source_latitude),
            CoordinateCache.source_longitude == normalize_coordinate(source_longitude),
        ))

    def save_coordinate(self, source_crs: str, target_crs: str, source_latitude: float,
                        source_longitude: float, target_latitude: float, target_longitude: float,
                        provider: str = DEFAULT_PROVIDER) -> CoordinateCache:
        statement = postgresql_insert(CoordinateCache).values(
            provider=provider,
            source_crs=source_crs,
            target_crs=target_crs,
            source_latitude=normalize_coordinate(source_latitude),
            source_longitude=normalize_coordinate(source_longitude),
            target_latitude=normalize_coordinate(target_latitude),
            target_longitude=normalize_coordinate(target_longitude),
        )
        statement = statement.on_conflict_do_update(
            constraint="uq_coordinate_cache_provider_crs_source",
            set_={
                "target_latitude": statement.excluded.target_latitude,
                "target_longitude": statement.excluded.target_longitude,
                "refreshed_at": func.now(),
            },
        ).returning(CoordinateCache.id)
        item_id = self.session.scalar(statement)
        return self.session.get(CoordinateCache, item_id, populate_existing=True)

    def hit_coordinate(self, item: CoordinateCache) -> CoordinateCache:
        self.session.execute(update(CoordinateCache).where(
            CoordinateCache.id == item.id,
        ).values(
            hit_count=CoordinateCache.hit_count + 1,
            last_hit_at=datetime.now(timezone.utc),
        ))
        self.session.flush()
        self.session.refresh(item)
        return item

    def stats(self) -> dict[str, int]:
        return {
            "search_count": self.session.scalar(select(func.count()).select_from(MapSearchCache)) or 0,
            "coordinate_count": self.session.scalar(select(func.count()).select_from(CoordinateCache)) or 0,
            "search_hit_count": self.session.scalar(select(func.coalesce(func.sum(MapSearchCache.hit_count), 0))) or 0,
        }

    def list_searches(self) -> list[MapSearchCache]:
        return list(self.session.scalars(select(MapSearchCache).order_by(MapSearchCache.id)))

    def list_coordinates(self) -> list[CoordinateCache]:
        return list(self.session.scalars(select(CoordinateCache).order_by(CoordinateCache.id)))

    def delete_search(self, item_id: int) -> None:
        self.session.execute(delete(MapSearchCache).where(MapSearchCache.id == item_id))
        self.session.flush()

    def delete_coordinate(self, item_id: int) -> None:
        self.session.execute(delete(CoordinateCache).where(CoordinateCache.id == item_id))
        self.session.flush()

    def clear(self) -> None:
        self.session.execute(delete(MapSearchCache))
        self.session.execute(delete(CoordinateCache))
        self.session.flush()
