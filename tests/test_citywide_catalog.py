from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select

from app.citywide.catalog import CitywideCatalog, CitywideModelFilters, ModelLibraryFilters
from app.models import (
    City,
    CitywideModelSummary,
    CitywideOffer,
    CitywideScanPoint,
    CitywideScanRun,
    CitywideScanStatus,
    Department,
    PersonalState,
    PersonalVehicleState,
    VehicleModel,
)


PICKUP = datetime(2026, 9, 6, 9, tzinfo=UTC)
RETURN = datetime(2026, 9, 7, 9, tzinfo=UTC)


def _fixture(session):
    city = City(zuche_city_id="14", name="广州", latitude=23.1291, longitude=113.2644)
    session.add(city)
    session.flush()
    fish = Department(
        city_id=city.id, zuche_dept_id=79340, name="鱼珠地铁站服务点",
        latitude=23.101610, longitude=113.432649)
    far = Department(
        city_id=city.id, zuche_dept_id=2033, name="东圃服务点",
        latitude=23.1234, longitude=113.4012)
    session.add_all([fish, far])
    session.flush()
    sea_lion = VehicleModel(
        zuche_model_id=4952, name="比亚迪海狮05", energy_type="新能源",
        energy_subtype="插电混动", energy_source="AI_FOCUSED",
        energy_confidence="HIGH", energy_updated_at=datetime(2026, 9, 2, tzinfo=UTC),
        first_seen_at=datetime(2026, 8, 1, tzinfo=UTC),
        last_seen_at=datetime(2026, 9, 2, tzinfo=UTC))
    archive = VehicleModel(
        zuche_model_id=4000, name="历史车型", energy_type="燃油",
        first_seen_at=datetime(2026, 7, 1, tzinfo=UTC),
        last_seen_at=datetime(2026, 8, 1, tzinfo=UTC))
    session.add_all([sea_lion, archive])
    session.flush()
    completed = CitywideScanRun(
        city_id=city.id, pickup_time=PICKUP, return_time=RETURN,
        status=CitywideScanStatus.COMPLETED, planned_point_count=2,
        completed_point_count=2)
    partial = CitywideScanRun(
        city_id=city.id, pickup_time=PICKUP, return_time=RETURN,
        status=CitywideScanStatus.PARTIAL, planned_point_count=2,
        completed_point_count=1, failed_point_count=1)
    session.add_all([completed, partial])
    session.flush()
    point = CitywideScanPoint(run_id=completed.id, department_id=fish.id)
    session.add(point)
    session.flush()
    session.add_all([
        CitywideOffer(
            run_id=completed.id, vehicle_model_id=sea_lion.id,
            department_id=fish.id, source_point_id=point.id,
            package_price=128, distance_from_yuzhu_km=Decimal("0.000"),
            book_flag=True),
        CitywideOffer(
            run_id=completed.id, vehicle_model_id=sea_lion.id,
            department_id=far.id, source_point_id=point.id,
            package_price=148, distance_from_yuzhu_km=Decimal("4.100"),
            book_flag=True),
        CitywideModelSummary(
            run_id=completed.id, vehicle_model_id=sea_lion.id,
            available_department_count=2, average_price=138,
            minimum_price=128, maximum_price=148,
            nearest_distance_km=0, nearest_department_id=fish.id),
        PersonalVehicleState(
            vehicle_model_id=sea_lion.id, state=PersonalState.WANT_TO_RENT),
    ])
    session.flush()
    return completed, partial, sea_lion, archive, fish


def test_citywide_catalog_returns_one_model_row_with_price_signal(session):
    """若目录按网点返回多行，车型页会重复且均价含义不清。"""
    completed, _, _, _, _ = _fixture(session)

    result = CitywideCatalog(session).search(CitywideModelFilters(run_id=completed.id))
    sea_lion = next(item for item in result["items"] if item["model_name"] == "比亚迪海狮05")

    assert sea_lion["availability"] == "AVAILABLE"
    assert sea_lion["department_count"] == 2
    assert sea_lion["average_price"] == 138.0
    assert sea_lion["minimum_price"] == 128.0
    assert sea_lion["maximum_price"] == 148.0
    assert sea_lion["has_price_difference"] is True
    assert sea_lion["personal_state"] == "WANT_TO_RENT"
    assert sea_lion["energy_subtype"] == "插电混动"
    assert sea_lion["energy_source"] == "AI_FOCUSED"
    assert sea_lion["energy_confidence"] == "HIGH"
    assert sea_lion["energy_updated_at"] == "2026-09-02T00:00:00+00:00"


def test_library_and_citywide_share_the_same_enriched_energy_fields(session):
    completed, _, sea_lion, _, _ = _fixture(session)
    catalog = CitywideCatalog(session)

    citywide_item = next(item for item in catalog.search(
        CitywideModelFilters(run_id=completed.id))["items"]
        if item["model_id"] == sea_lion.zuche_model_id)
    library_item = next(item for item in catalog.library(
        ModelLibraryFilters())["items"]
        if item["model_id"] == sea_lion.zuche_model_id)

    for item in (citywide_item, library_item):
        assert item["energy_type"] == "新能源"
        assert item["energy_subtype"] == "插电混动"
        assert item["energy_source"] == "AI_FOCUSED"
        assert item["energy_confidence"] == "HIGH"


def test_unseen_model_status_depends_on_run_completeness(session):
    """若部分扫描把未出现车型判为无车，会产生错误否定结论。"""
    completed, partial, _, archive, _ = _fixture(session)
    catalog = CitywideCatalog(session)

    complete_item = next(item for item in catalog.search(
        CitywideModelFilters(run_id=completed.id))["items"]
        if item["model_id"] == archive.zuche_model_id)
    partial_item = next(item for item in catalog.search(
        CitywideModelFilters(run_id=partial.id))["items"]
        if item["model_id"] == archive.zuche_model_id)

    assert complete_item["availability"] == "NOT_FOUND"
    assert partial_item["availability"] == "INCOMPLETE"

    incomplete_only = catalog.search(CitywideModelFilters(
        run_id=partial.id, availability="INCOMPLETE"))
    not_found_only = catalog.search(CitywideModelFilters(
        run_id=completed.id, availability="NOT_FOUND"))
    assert {item["model_id"] for item in incomplete_only["items"]} == {
        4952, archive.zuche_model_id}
    assert [item["model_id"] for item in not_found_only["items"]] == [
        archive.zuche_model_id]


def test_department_filter_and_offers_use_database_rows(session):
    """若网点筛选不使用 EXISTS 或报价未按鱼珠距离排序，展开结果会失真。"""
    completed, _, sea_lion, archive, fish = _fixture(session)
    catalog = CitywideCatalog(session)

    filtered = catalog.search(CitywideModelFilters(
        run_id=completed.id, department_id=fish.id))
    offers = catalog.offers(completed.id, sea_lion.zuche_model_id)

    assert [item["model_id"] for item in filtered["items"]] == [sea_lion.zuche_model_id]
    assert [item["department_name"] for item in offers] == ["鱼珠地铁站服务点", "东圃服务点"]
    assert catalog.offers(completed.id, archive.zuche_model_id) == []


def test_permanent_library_keeps_models_missing_from_current_run(session):
    """若车型库从当前报价开始查询，历史车型会被错误删除。"""
    completed, _, sea_lion, archive, _ = _fixture(session)

    result = CitywideCatalog(session).library(ModelLibraryFilters(
        run_id=completed.id, page_size=50))

    assert {item["model_id"] for item in result["items"]} == {
        sea_lion.zuche_model_id, archive.zuche_model_id}
    detail = CitywideCatalog(session).detail(archive.zuche_model_id, completed.id)
    assert detail["availability"] == "NOT_FOUND"
    assert detail["history"] == []


def test_permanent_library_can_filter_by_model_name_without_a_run(session):
    """车型库首页没有租期，按车名筛选仍必须有明确的查询起点。"""
    _fixture(session)

    result = CitywideCatalog(session).library(ModelLibraryFilters(
        q="比亚迪海狮05", page_size=50))

    assert [item["model_name"] for item in result["items"]] == ["比亚迪海狮05"]


def test_permanent_library_reports_collection_totals_and_sorts_by_name(session):
    """若车型库没有全库汇总和车型名排序，页面仍会围绕租期而不是逛车。"""
    _fixture(session)
    session.add(VehicleModel(
        zuche_model_id=4953,
        name="比亚迪海狮05",
        latest_description="纯电 65kWh | SUV 5座",
        first_seen_at=datetime.now(UTC),
        last_seen_at=datetime.now(UTC),
    ))
    session.flush()

    result = CitywideCatalog(session).library(ModelLibraryFilters(
        sort="name", page_size=50))

    assert result["summary"]["variant_count"] == 3
    assert result["summary"]["model_name_count"] == 2
    assert result["summary"]["new_last_7_days"] >= 1
    assert [item["model_name"] for item in result["items"]] == [
        "历史车型", "比亚迪海狮05", "比亚迪海狮05"]
    sea_lion_variants = [
        item for item in result["items"] if item["model_name"] == "比亚迪海狮05"]
    assert {item["model_id"] for item in sea_lion_variants} == {4952, 4953}
    assert next(item for item in sea_lion_variants if item["model_id"] == 4953)[
        "description"] == "纯电 65kWh | SUV 5座"
