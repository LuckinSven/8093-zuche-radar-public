from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.energy import classify_energy
from app.repositories.discovery import DiscoveryRepository


class DiscoveryFilters(BaseModel):
    scan_id: UUID
    group_id: int | None = None
    min_price: Decimal | None = Field(default=None, ge=0)
    max_price: Decimal | None = Field(default=None, ge=0)
    max_distance_km: float | None = Field(default=None, ge=0)
    bookable: bool | None = None
    body_style: str | None = None
    seat_count: int | None = Field(default=None, ge=1)
    energy_type: str | None = None
    personal_state: str | None = None
    change_type: str | None = None


class DepartmentOffer(BaseModel):
    department_id: int
    department_name: str
    distance_km: float | None
    daily_price: Decimal | None
    package_price: Decimal | None
    bookable: bool


class VehicleCard(BaseModel):
    model_id: int
    model_name: str
    lowest_price: Decimal | None
    nearest_department: str
    nearest_distance_km: float | None
    model_desc: str | None
    body_style: str | None
    seat_count: int | None
    energy_type: str | None
    native_groups: list[str]
    native_group_ids: list[int]
    changes: list[str]
    personal_state: str | None
    offers: list[DepartmentOffer]
    price_is_final: bool = False


class DiscoveryService:
    def __init__(self, session: Session) -> None:
        self.repository = DiscoveryRepository(session)

    def search(self, filters: DiscoveryFilters) -> list[VehicleCard]:
        groups: dict[int, list[tuple[int, str]]] = {}
        for model_id, group_id, name in self.repository.group_rows(filters.scan_id):
            groups.setdefault(model_id, []).append((group_id, name))
        changes: dict[int, list[str]] = {}
        for model_id, event_type in self.repository.event_rows(filters.scan_id):
            changes.setdefault(model_id, []).append(event_type)
        states = self.repository.personal_states()
        cards: dict[int, VehicleCard] = {}
        for snapshot, model, department in self.repository.snapshot_rows(filters.scan_id):
            distance = _snapshot_distance(snapshot)
            offer = DepartmentOffer(department_id=department.zuche_dept_id, department_name=department.name,
                distance_km=distance, daily_price=snapshot.daily_price, package_price=snapshot.package_price,
                bookable=snapshot.book_flag)
            card = cards.get(model.id)
            if card is None:
                group_values = groups.get(model.id, [])
                parsed = _derived(snapshot.model_desc, model.name, [x[1] for x in group_values])
                card = VehicleCard(model_id=model.zuche_model_id, model_name=model.name,
                    lowest_price=snapshot.package_price or snapshot.daily_price, nearest_department=department.name,
                    nearest_distance_km=distance, model_desc=snapshot.model_desc, body_style=parsed[0],
                    seat_count=parsed[1], energy_type=parsed[2], native_groups=[x[1] for x in group_values],
                    native_group_ids=[x[0] for x in group_values], changes=changes.get(model.id, []),
                    personal_state=states.get(model.id, "UNTRIED"), offers=[offer])
                cards[model.id] = card
            else:
                card.offers.append(offer)
                price = snapshot.package_price or snapshot.daily_price
                if price is not None and (card.lowest_price is None or price < card.lowest_price): card.lowest_price = price
                if distance is not None and (card.nearest_distance_km is None or distance < card.nearest_distance_km):
                    card.nearest_distance_km, card.nearest_department = distance, department.name
        if filters.change_type == "DISAPPEARED":
            cards = {}
            disappeared_groups: dict[int, list[tuple[int, str]]] = {}
            for model_id, group_id, name in self.repository.disappeared_group_rows(filters.scan_id):
                disappeared_groups.setdefault(model_id, []).append((group_id, name))
            for snapshot, model, department in self.repository.disappeared_rows(filters.scan_id):
                distance = _snapshot_distance(snapshot)
                offer = DepartmentOffer(department_id=department.zuche_dept_id,
                    department_name=department.name, distance_km=distance,
                    daily_price=snapshot.daily_price, package_price=snapshot.package_price,
                    bookable=False)
                card = cards.get(model.id)
                if card is None:
                    group_values = disappeared_groups.get(model.id, [])
                    parsed = _derived(snapshot.model_desc, model.name, [x[1] for x in group_values])
                    cards[model.id] = VehicleCard(model_id=model.zuche_model_id, model_name=model.name,
                        lowest_price=snapshot.package_price or snapshot.daily_price,
                        nearest_department=department.name, nearest_distance_km=distance,
                        model_desc=snapshot.model_desc, body_style=parsed[0], seat_count=parsed[1],
                        energy_type=parsed[2], native_groups=[x[1] for x in group_values],
                        native_group_ids=[x[0] for x in group_values], changes=["DISAPPEARED"],
                        personal_state=states.get(model.id, "UNTRIED"), offers=[offer])
                else:
                    card.offers.append(offer)
                    price = snapshot.package_price or snapshot.daily_price
                    if price is not None and (card.lowest_price is None or price < card.lowest_price):
                        card.lowest_price = price
                    if distance is not None and (card.nearest_distance_km is None or distance < card.nearest_distance_km):
                        card.nearest_distance_km, card.nearest_department = distance, department.name
        return sorted((x for x in cards.values() if _matches(x, filters)), key=lambda x: (x.lowest_price is None, x.lowest_price or 0, x.model_name))

    def list_groups(self, scan_id: UUID) -> list[dict]:
        values = {(group_id, name) for _, group_id, name in self.repository.group_rows(scan_id)}
        values.update((group_id, name) for _, group_id, name in self.repository.disappeared_group_rows(scan_id))
        return [{"id": group_id, "name": name} for group_id, name in sorted(values, key=lambda x: (x[1], x[0]))]

    def get_model_detail(self, model_id: int) -> dict | None:
        model = self.repository.model_by_external_id(model_id)
        if model is None:
            return None
        latest_scan_id = self.repository.latest_scan_id_for_model(model.id)
        current = None
        if latest_scan_id is not None:
            current = next((item for item in self.search(DiscoveryFilters(scan_id=latest_scan_id))
                            if item.model_id == model_id), None)
        history: dict[str, dict] = {}
        for snapshot, department, run in self.repository.model_history_rows(model.id):
            key = str(run.id)
            price = snapshot.package_price or snapshot.daily_price
            item = history.setdefault(key, {"scan_id": key, "started_at": run.started_at,
                "location_name": run.location_name, "lowest_price": price,
                "department_name": department.name, "bookable": snapshot.book_flag})
            if price is not None and (item["lowest_price"] is None or price < item["lowest_price"]):
                item["lowest_price"] = price
                item["department_name"] = department.name
            item["bookable"] = item["bookable"] or snapshot.book_flag
        source = current.model_dump(mode="json") if current else {"offers": [], "price_is_final": False}
        personal = self.repository.personal_state(model.id)
        personal_data = {"state": str(personal.state), "note": personal.note,
                         "rented_on": personal.rented_on} if personal else {
                             "state": "UNTRIED", "note": None, "rented_on": None}
        return {"model_id": model.zuche_model_id, "model_name": model.name, "source": source,
                "energy": {
                    "type": model.energy_type,
                    "subtype": model.energy_subtype,
                    "source": model.energy_source,
                    "confidence": model.energy_confidence,
                    "updated_at": model.energy_updated_at,
                },
                "personal": personal_data, "history": list(history.values())}


def _matches(card: VehicleCard, f: DiscoveryFilters) -> bool:
    if f.group_id is not None and f.group_id not in card.native_group_ids: return False
    if f.min_price is not None and (card.lowest_price is None or card.lowest_price < f.min_price): return False
    if f.max_price is not None and (card.lowest_price is None or card.lowest_price > f.max_price): return False
    if f.max_distance_km is not None and (card.nearest_distance_km is None or card.nearest_distance_km > f.max_distance_km): return False
    if f.bookable is not None and not any(x.bookable == f.bookable for x in card.offers): return False
    if f.body_style and card.body_style != f.body_style: return False
    if f.seat_count and card.seat_count != f.seat_count: return False
    if f.energy_type and card.energy_type != f.energy_type: return False
    if f.personal_state and card.personal_state != f.personal_state: return False
    if f.change_type and f.change_type not in card.changes: return False
    return True


def _distance(value: str | None) -> float | None:
    try: return float((value or "").removesuffix("km"))
    except ValueError: return None


def _snapshot_distance(snapshot) -> float | None:
    if snapshot.department_distance_km is not None:
        return float(snapshot.department_distance_km)
    return _distance(snapshot.department_distance)


def _derived(desc: str | None, model_name: str | None = None,
             native_group_names: list[str] | None = None):
    import re
    body = re.search(r"(SUV|MPV|两厢|三厢|跑车|皮卡)(?=\d座|\s|$)", desc or "", re.I)
    seats = re.search(r"(\d)座", desc or "")
    energy = classify_energy(desc, model_name, native_group_names or [])
    return body.group(1).upper() if body else None, int(seats.group(1)) if seats else None, energy
