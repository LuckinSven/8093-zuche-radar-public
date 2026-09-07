from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Event

from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

from app.integrations.map_cache_repository import MapCacheRepository


def test_search_cache_keeps_latest_result_and_counts_hits(session):
    """Replacing a cached result must not reset its accumulated hit count."""
    repo = MapCacheRepository(session)

    item = repo.save_search("GuangZhou", " 三溪  地铁站 ", [{"name": "旧结果"}])
    hit = repo.hit_search(item)
    updated = repo.save_search("GUANGZHOU", "三溪 地铁站", [{"name": "新结果"}])

    assert updated.id == item.id
    assert updated.results_json == [{"name": "新结果"}]
    assert updated.hit_count == 1
    assert updated.region == "GUANGZHOU"
    assert updated.keyword == "三溪 地铁站"
    assert hit.last_hit_at is not None
    assert repo.get_search("guangzhou", "三溪 地铁站").id == item.id


def test_search_cache_keeps_significant_single_spaces_distinct(session):
    """Collapsed whitespace must not conflate a no-space keyword with a spaced one."""
    repo = MapCacheRepository(session)

    no_space = repo.save_search("广州", "三溪地铁站", [{"name": "无空格"}])
    spaced = repo.save_search("广州", "三溪 地铁站", [{"name": "有空格"}])

    assert spaced.id != no_space.id
    assert repo.get_search("广州", "三溪地铁站").results_json == [{"name": "无空格"}]


def test_coordinate_cache_is_unique_by_crs_and_normalized_coordinate(session):
    """A source-coordinate change below eight decimal places must update one row."""
    repo = MapCacheRepository(session)

    first = repo.save_coordinate(
        "BD-09", "GCJ-02", 23.110319261, 113.422343706, 23.104090425, 113.415894899)
    second = repo.save_coordinate(
        "BD-09", "GCJ-02", 23.1103192611, 113.4223437061, 23.1041, 113.4159)

    assert second.id == first.id
    assert second.source_latitude == Decimal("23.11031926")
    assert second.target_latitude == Decimal("23.1041")


def test_coordinate_cache_counts_hits_and_records_last_hit_time(session):
    """A coordinate cache read must atomically count usage and record when it was used."""
    repo = MapCacheRepository(session)
    item = repo.save_coordinate(
        "BD-09", "GCJ-02", 23.109, 113.422, 23.103, 113.416)

    updated = repo.hit_coordinate(item)

    assert updated.hit_count == 1
    assert updated.last_hit_at is not None


def test_cache_repository_lists_deletes_and_clears_entries(session):
    """Repository administration must affect persisted search and coordinate entries."""
    repo = MapCacheRepository(session)
    search = repo.save_search("广州", "天河", [{"name": "天河"}])
    coordinate = repo.save_coordinate(
        "BD-09", "GCJ-02", 23.1, 113.4, 23.09, 113.39)

    assert repo.stats() == {"search_count": 1, "coordinate_count": 1, "search_hit_count": 0}
    assert [item.id for item in repo.list_searches()] == [search.id]
    assert [item.id for item in repo.list_coordinates()] == [coordinate.id]

    repo.delete_search(search.id)
    repo.delete_coordinate(coordinate.id)
    assert repo.stats() == {"search_count": 0, "coordinate_count": 0, "search_hit_count": 0}

    repo.save_search("广州", "珠江新城", [])
    repo.save_coordinate("BD-09", "GCJ-02", 23.2, 113.5, 23.19, 113.49)
    repo.clear()
    assert repo.stats() == {"search_count": 0, "coordinate_count": 0, "search_hit_count": 0}


def test_concurrent_search_writes_keep_one_row_and_latest_result(engine):
    """A second in-flight writer must atomically replace the first search result."""
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    first_saved, release_first, second_insert_started = Event(), Event(), Event()

    def observe_second_insert(_conn, _cursor, statement, _parameters, _context, _many):
        if first_saved.is_set() and "INSERT INTO map_search_caches" in statement:
            second_insert_started.set()

    def first_writer():
        with factory() as thread_session:
            item = MapCacheRepository(thread_session).save_search(
                "广州", "并发地点", [{"name": "先到结果"}])
            first_saved.set()
            assert release_first.wait(5)
            thread_session.commit()
            return item.id

    def second_writer():
        assert first_saved.wait(5)
        with factory() as thread_session:
            item = MapCacheRepository(thread_session).save_search(
                "广州", "并发地点", [{"name": "后到结果"}])
            thread_session.commit()
            return item.id

    event.listen(engine, "before_cursor_execute", observe_second_insert)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(first_writer)
            assert first_saved.wait(5)
            second = pool.submit(second_writer)
            assert second_insert_started.wait(5)
            release_first.set()
            first_id, second_id = first.result(5), second.result(5)
    finally:
        release_first.set()
        event.remove(engine, "before_cursor_execute", observe_second_insert)

    with factory() as verification_session:
        items = MapCacheRepository(verification_session).list_searches()
        assert first_id == second_id
        assert len(items) == 1
        assert items[0].results_json == [{"name": "后到结果"}]


def test_concurrent_coordinate_writes_keep_one_row_and_latest_result(engine):
    """A second in-flight writer must atomically replace the first converted coordinate."""
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    first_saved, release_first, second_insert_started = Event(), Event(), Event()

    def observe_second_insert(_conn, _cursor, statement, _parameters, _context, _many):
        if first_saved.is_set() and "INSERT INTO coordinate_caches" in statement:
            second_insert_started.set()

    def first_writer():
        with factory() as thread_session:
            item = MapCacheRepository(thread_session).save_coordinate(
                "BD-09", "GCJ-02", 23.109, 113.422, 23.103, 113.416)
            first_saved.set()
            assert release_first.wait(5)
            thread_session.commit()
            return item.id

    def second_writer():
        assert first_saved.wait(5)
        with factory() as thread_session:
            item = MapCacheRepository(thread_session).save_coordinate(
                "BD-09", "GCJ-02", 23.109, 113.422, 23.104, 113.417)
            thread_session.commit()
            return item.id

    event.listen(engine, "before_cursor_execute", observe_second_insert)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(first_writer)
            assert first_saved.wait(5)
            second = pool.submit(second_writer)
            assert second_insert_started.wait(5)
            release_first.set()
            first_id, second_id = first.result(5), second.result(5)
    finally:
        release_first.set()
        event.remove(engine, "before_cursor_execute", observe_second_insert)

    with factory() as verification_session:
        items = MapCacheRepository(verification_session).list_coordinates()
        assert first_id == second_id
        assert len(items) == 1
        assert items[0].target_latitude == Decimal("23.10400000")
        assert items[0].target_longitude == Decimal("113.41700000")
