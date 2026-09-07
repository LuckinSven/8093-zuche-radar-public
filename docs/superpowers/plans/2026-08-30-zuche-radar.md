# 神州车型雷达第一版 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a single-user Guangzhou-first web application that manually or optionally automatically scans the validated ShenZhou car-list API, preserves history, surfaces change events, and tracks personal vehicle preferences.

**Architecture:** A FastAPI monolith serves Jinja pages and JSON endpoints. A dedicated ShenZhou gateway client and parser feed a scan service that writes immutable scan batches, raw JSON payloads, availability snapshots, and derived events to PostgreSQL; the UI only queries stored results. APScheduler runs optional probe-level schedules inside the service now, with scanner code isolated so it can move to worker processes later.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, SQLAlchemy 2, Alembic, PostgreSQL 16, psycopg, httpx, APScheduler, pytest, pytest-asyncio, Docker Compose, vanilla JavaScript and CSS.

**Spec:** `docs/superpowers/specs/2026-08-30-zuche-radar-design.md`

## Global Constraints

- Project root is `/opt/apps/zuche-radar-8093`; the service port is `8093`.
- This is a single-user personal application: do not add authentication, authorization, user tables, or multi-tenancy.
- PostgreSQL is the source of truth; do not introduce SQLite as a runtime database.
- A model is identified by ShenZhou `modelId`, never by `modelName` alone.
- Retain normalized scan history indefinitely and compressed raw response JSON for exactly 60 days.
- Display `dailyPrice` and `packagePrice` as list/basic prices, never as final checkout prices.
- Store ShenZhou-provided `modelGroups` as historical facts; parse `modelDesc` only as a separately labelled derived value.
- Automatic probe schedules exist but are disabled by default; immediate scans are the primary flow.
- Never create an order, login session, or booking action against ShenZhou.

---

## Proposed file structure

```text
app/
  main.py                         # FastAPI app and lifespan wiring
  config.py                       # environment-backed settings
  db.py                           # engine/session lifecycle
  models.py                       # SQLAlchemy tables and enums
  schemas.py                      # Pydantic request/response models
  repositories.py                 # database reads and writes
  zuche_client.py                 # city resolver and gateway HTTP client
  zuche_parser.py                 # pure conversion of API payloads
  scanner.py                      # scan orchestration and event detection
  scheduler.py                    # optional enabled-probe scheduler
  services.py                     # query, personal-state, annotation services
  routes.py                       # JSON endpoints
  web.py                          # Jinja page routes
  templates/                      # discovery, detail, history, administration pages
  static/                         # CSS and browser JavaScript
alembic/                          # database migration environment
tests/
  fixtures/                       # recorded, redacted successful API payload
  test_*.py                       # unit, repository, HTTP and integration tests
docker-compose.yml                # PostgreSQL and application service
Dockerfile
pyproject.toml
.env.example
README.md
```

### Task 1: Bootstrap the runnable application and PostgreSQL environment

**Files:**
- Create: `pyproject.toml`
- Create: `.env.example`
- Create: `app/__init__.py`
- Create: `app/config.py`
- Create: `app/db.py`
- Create: `app/main.py`
- Create: `docker-compose.yml`
- Create: `Dockerfile`
- Create: `tests/test_health.py`
- Create: `README.md`

**Interfaces:**
- Produces `create_app() -> FastAPI`, `Settings`, `get_session()`, and `GET /healthz`.
- Consumes environment variable `DATABASE_URL`; Docker Compose supplies `postgresql+psycopg://zuche:zuche@db:5432/zuche_radar`.

- [ ] **Step 1: Write the failing health test**

```python
from fastapi.testclient import TestClient
from app.main import create_app

def test_healthz_reports_service_name():
    response = TestClient(create_app()).get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"service": "zuche-radar", "status": "ok"}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_health.py::test_healthz_reports_service_name -v`

Expected: FAIL because `app.main` does not exist.

- [ ] **Step 3: Implement minimal configuration and application startup**

```python
# app/main.py
from fastapi import FastAPI

def create_app() -> FastAPI:
    app = FastAPI(title="神州车型雷达")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"service": "zuche-radar", "status": "ok"}
    return app

app = create_app()
```

Add pinned runtime and dev dependencies, a settings object with `DATABASE_URL`, `APP_PORT=8093`, `RAW_RETENTION_DAYS=60`, a SQLAlchemy session factory, a PostgreSQL 16 Compose service with a named volume, and README commands for `docker compose up --build` and `pytest`.

- [ ] **Step 4: Run verification**

Run: `pytest tests/test_health.py -v && docker compose config -q`

Expected: PASS and Compose configuration validates.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml .env.example app tests/test_health.py docker-compose.yml Dockerfile README.md
git commit -m "feat: bootstrap zuche radar service"
```

### Task 2: Add the PostgreSQL schema, migrations, and repository boundaries

**Files:**
- Create: `alembic.ini`
- Create: `alembic/env.py`
- Create: `alembic/versions/0001_initial_schema.py`
- Create: `app/models.py`
- Create: `app/repositories.py`
- Create: `tests/test_repositories.py`
- Modify: `app/db.py`

**Interfaces:**
- Produces tables `cities`, `probes`, `scan_runs`, `raw_payloads`, `departments`, `vehicle_models`, `availability_snapshots`, `model_group_memberships`, `personal_vehicle_states`, `vehicle_annotations`, and `change_events`.
- Produces `ScanRepository.create_run()`, `persist_successful_scan()`, `mark_run_failed()`, and `purge_expired_raw_payloads(cutoff)`.
- Consumes `ParsedScan` defined in Task 3 and emits primary keys used by Tasks 4–7.

- [ ] **Step 1: Write failing persistence tests**

```python
def test_same_model_name_with_two_model_ids_creates_two_models(session):
    repo = ScanRepository(session)
    repo.upsert_vehicle_model(model_id=100, model_name="比亚迪秦PLUS")
    repo.upsert_vehicle_model(model_id=101, model_name="比亚迪秦PLUS")
    assert repo.count_vehicle_models() == 2

def test_raw_payload_purge_only_deletes_rows_older_than_60_days(session):
    deleted = ScanRepository(session).purge_expired_raw_payloads(
        cutoff=datetime(2026, 6, 30, tzinfo=UTC)
    )
    assert deleted == 1
```

- [ ] **Step 2: Run the repository tests to verify failure**

Run: `pytest tests/test_repositories.py -v`

Expected: FAIL because models and repository functions do not exist.

- [ ] **Step 3: Implement schema and first Alembic migration**

Use UUID primary keys for scan runs; preserve ShenZhou numeric IDs in unique indexed columns. Give every snapshot a scan-run foreign key and a uniqueness constraint on `(scan_run_id, department_id, model_id)`. Store raw payload content as `BYTEA` compressed with gzip plus `captured_at`; use `captured_at < cutoff` for retention. Define `PersonalState` enum as `UNTRIED`, `WANT_TO_RENT`, `RENTED`, `LIKED`, `NOT_CONSIDERING`; annotations have `field_name`, `value`, `source`, `confidence`, and `verified_at`.

- [ ] **Step 4: Run migrations and repository tests**

Run: `docker compose up -d db && alembic upgrade head && pytest tests/test_repositories.py -v`

Expected: migration succeeds; both tests PASS against PostgreSQL.

- [ ] **Step 5: Commit**

```bash
git add alembic.ini alembic app/models.py app/repositories.py app/db.py tests/test_repositories.py
git commit -m "feat: add scan history data model"
```

### Task 3: Implement the ShenZhou gateway client and pure response parser

**Files:**
- Create: `app/zuche_client.py`
- Create: `app/zuche_parser.py`
- Create: `tests/fixtures/guangzhou_choose_car_v3.json`
- Create: `tests/test_zuche_parser.py`
- Create: `tests/test_zuche_client.py`

**Interfaces:**
- Produces `ZucheClient.resolve_city(lat: float, lon: float) -> CityResolution` and `ZucheClient.choose_car(request: ScanRequest) -> dict`.
- Produces `parse_choose_car(payload: dict, request: ScanRequest) -> ParsedScan`.
- `ParsedScan` contains `departments`, `offers`, and `group_memberships`; every offer contains `model_id`, `model_name`, `daily_price`, `package_price`, `book_flag`, `inventory_type`, `model_desc`, and the source group IDs.

- [ ] **Step 1: Add a failing parser test using the recorded response**

```python
def test_parser_preserves_prices_distance_and_group_membership(guangzhou_payload):
    parsed = parse_choose_car(guangzhou_payload, REQUEST)
    offer = next(x for x in parsed.offers if x.model_id == 4677)
    assert offer.model_name == "小鹏P7+"
    assert offer.daily_price == Decimal("288")
    assert offer.department_distance == "2.11km"
    assert 16 in offer.group_ids
```

- [ ] **Step 2: Run parser tests to verify failure**

Run: `pytest tests/test_zuche_parser.py -v`

Expected: FAIL because parser and fixture are absent.

- [ ] **Step 3: Implement request wire format and parser**

Implement the validated gateway request: POST to `/api/gw.do?uri=/resource/carrctapi/order/chooseCar/v3`, form-encode `data=json.dumps(payload, ensure_ascii=False)`, and send mobile user agent, origin and referer headers. Resolve cities with `/action/carrctapi/order/cityLocation/v1`. Require top-level `status == "SUCCESS"` and `code == 1`; otherwise raise `ZucheResponseError` carrying the safe response status and message. Parse group membership from `content.modelGroups` by `modelId`; do not infer a group from names. Redact cookies, request IDs and timestamps from the fixture while retaining the fields needed by tests.

- [ ] **Step 4: Add HTTP contract tests with mocked transport**

```python
def test_choose_car_posts_form_encoded_json_to_gateway(httpx_mock):
    httpx_mock.add_response(json={"status": "SUCCESS", "code": 1, "content": {}})
    ZucheClient().choose_car(REQUEST)
    request = httpx_mock.get_request()
    assert "uri=/resource/carrctapi/order/chooseCar/v3" in str(request.url)
    assert request.headers["content-type"].startswith("application/x-www-form-urlencoded")
    assert '"pickupCityId":"14"' in request.content.decode()
```

Run: `pytest tests/test_zuche_parser.py tests/test_zuche_client.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/zuche_client.py app/zuche_parser.py tests/fixtures tests/test_zuche_parser.py tests/test_zuche_client.py
git commit -m "feat: add validated zuche gateway adapter"
```

### Task 4: Orchestrate scans, persist outcomes, and derive change events

**Files:**
- Create: `app/scanner.py`
- Create: `app/schemas.py`
- Create: `tests/test_scanner.py`
- Modify: `app/repositories.py`

**Interfaces:**
- Produces `ScanService.run(request: ScanRequest, trigger: ScanTrigger) -> ScanRunResult`.
- Produces change types `FIRST_SEEN`, `REAPPEARED`, `DISAPPEARED`, `PRICE_CHANGED`, and `CLOSER_DEPARTMENT`.
- Consumes `ZucheClient`, `parse_choose_car`, and `ScanRepository`.

- [ ] **Step 1: Write failing scan-service tests**

```python
def test_first_successful_offer_creates_first_seen_event(scanner, request):
    result = scanner.run(request, ScanTrigger.MANUAL)
    assert result.status == "SUCCESS"
    assert {event.event_type for event in result.events} == {"FIRST_SEEN"}

def test_failed_gateway_call_marks_run_failed_without_deleting_previous_snapshots(scanner, request):
    scanner.client.choose_car.side_effect = ZucheResponseError("TIMEOUT")
    result = scanner.run(request, ScanTrigger.MANUAL)
    assert result.status == "FAILED"
    assert scanner.repository.count_snapshots() == 3
```

- [ ] **Step 2: Run the scanner tests to verify failure**

Run: `pytest tests/test_scanner.py -v`

Expected: FAIL because `ScanService` does not exist.

- [ ] **Step 3: Implement one shared manual/automatic scan flow**

Create a `PENDING` run before HTTP work. On success, gzip the raw payload, upsert city/departments/models, add immutable snapshots and group memberships, compare with the immediately previous successful run having the same location and rental window, create events, and mark the run `SUCCESS`. On failure, mark it `FAILED` with a bounded error code and leave earlier snapshots intact. Use at most two retries for transport timeout or 5xx errors with exponential waits of 1 and 2 seconds; do not retry 4xx or structurally invalid responses.

- [ ] **Step 4: Run focused verification**

Run: `pytest tests/test_scanner.py tests/test_repositories.py -v`

Expected: PASS, including successful persistence and failure preservation.

- [ ] **Step 5: Commit**

```bash
git add app/scanner.py app/schemas.py app/repositories.py tests/test_scanner.py
git commit -m "feat: persist scans and detect vehicle changes"
```

### Task 5: Expose manual scans, query filters, history, and optional scheduling APIs

**Files:**
- Create: `app/routes.py`
- Create: `app/scheduler.py`
- Create: `tests/test_routes.py`
- Create: `tests/test_scheduler.py`
- Modify: `app/main.py`

**Interfaces:**
- Produces `POST /api/scans`, `GET /api/scans/{run_id}`, `GET /api/discovery`, `GET /api/history`, `GET|POST|PATCH /api/probes`.
- `POST /api/scans` accepts `location_name`, `lat`, `lon`, `pickup_time`, `return_time`; it returns scan ID, status, counts and events.
- `GET /api/discovery` accepts `scan_id`, `group_id`, `max_distance_km`, `min_price`, `max_price`, `personal_state`, `availability`, and `change_type`.
- `ProbeScheduler.sync(probes)` schedules only probes where `enabled is True`.

- [ ] **Step 1: Write failing endpoint and scheduler tests**

```python
def test_manual_scan_endpoint_returns_counts(client, scanner):
    response = client.post("/api/scans", json=SCAN_BODY)
    assert response.status_code == 201
    assert response.json()["department_count"] == 7

def test_disabled_probe_is_not_added_to_scheduler(scheduler, disabled_probe):
    scheduler.sync([disabled_probe])
    assert scheduler.job_ids() == []
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/test_routes.py tests/test_scheduler.py -v`

Expected: FAIL because routes and scheduler do not exist.

- [ ] **Step 3: Implement the API and scheduler**

Validate `return_time > pickup_time`, valid latitude/longitude ranges, and positive price/distance bounds. Return 422 with field-level details for invalid requests. Discovery queries must return source fields and parsed fields separately, and apply all supplied filters conjunctively. Add `/api/probes` validation for a valid cron expression or positive interval. Use APScheduler’s SQLAlchemy job store only after repository persistence; on application startup reconcile enabled probes and do not schedule disabled ones.

- [ ] **Step 4: Run verification**

Run: `pytest tests/test_routes.py tests/test_scheduler.py tests/test_scanner.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/routes.py app/scheduler.py app/main.py tests/test_routes.py tests/test_scheduler.py
git commit -m "feat: add scan discovery and probe APIs"
```

### Task 6: Add personal vehicle states and trustworthy manual annotations

**Files:**
- Create: `app/services.py`
- Create: `tests/test_personal_data.py`
- Modify: `app/routes.py`
- Modify: `app/schemas.py`
- Modify: `app/repositories.py`

**Interfaces:**
- Produces `PUT /api/models/{model_id}/personal-state` and `POST|GET /api/models/{model_id}/annotations`.
- `PersonalVehicleService.set_state(model_id, state, note, rented_on)` returns the current state record.
- `AnnotationService.add(model_id, field_name, value, source, confidence, verified_at)` returns one immutable annotation revision.

- [ ] **Step 1: Write failing personal-data tests**

```python
def test_setting_want_to_rent_is_visible_in_discovery(client, known_model):
    response = client.put(f"/api/models/{known_model.model_id}/personal-state", json={"state": "WANT_TO_RENT"})
    assert response.status_code == 200
    cards = client.get("/api/discovery", params={"personal_state": "WANT_TO_RENT"}).json()["items"]
    assert [card["model_id"] for card in cards] == [known_model.model_id]

def test_annotation_keeps_source_and_confidence(client, known_model):
    response = client.post(f"/api/models/{known_model.model_id}/annotations", json={
        "field_name": "energy_type", "value": "插电混动", "source": "本人核对", "confidence": "HIGH"
    })
    assert response.json()["source"] == "本人核对"
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/test_personal_data.py -v`

Expected: FAIL because personal-data services and endpoints do not exist.

- [ ] **Step 3: Implement state and annotation services**

Allow exactly one current personal state per `model_id`, retaining update timestamps and optional note/rental date. Require non-empty annotation field/value/source and validate confidence as `LOW`, `MEDIUM`, or `HIGH`. Never overwrite source API fields: present annotations in a separate `manual_annotations` collection in responses.

- [ ] **Step 4: Run verification**

Run: `pytest tests/test_personal_data.py tests/test_routes.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services.py app/routes.py app/schemas.py app/repositories.py tests/test_personal_data.py
git commit -m "feat: add personal vehicle states and annotations"
```

### Task 7: Build the discovery, detail, history, and administration pages

**Files:**
- Create: `app/web.py`
- Create: `app/templates/base.html`
- Create: `app/templates/discovery.html`
- Create: `app/templates/model_detail.html`
- Create: `app/templates/history.html`
- Create: `app/templates/admin.html`
- Create: `app/static/app.css`
- Create: `app/static/app.js`
- Create: `tests/test_web_pages.py`
- Modify: `app/main.py`

**Interfaces:**
- Produces routes `GET /`, `GET /models/{model_id}`, `GET /history`, and `GET /admin`.
- Browser JavaScript calls Task 5 APIs only; it never calls ShenZhou directly.
- Produces time presets `this_saturday`, `next_saturday`, `this_weekend`, and `custom`, all defaulting to 09:00 boundaries.

- [ ] **Step 1: Write failing page tests**

```python
def test_discovery_page_exposes_scan_form_and_next_saturday_preset(client):
    response = client.get("/")
    assert response.status_code == 200
    assert 'data-preset="next_saturday"' in response.text
    assert 'id="manual-scan-form"' in response.text

def test_model_detail_identifies_list_price_as_not_final_checkout_price(client):
    response = client.get("/models/4677")
    assert "非最终结算价" in response.text
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/test_web_pages.py -v`

Expected: FAIL because web routes/templates do not exist.

- [ ] **Step 3: Implement accessible pages and client behavior**

Discovery starts with Guangzhou common locations, accepts free text location/metro station or explicit coordinates, shows date choices with default `09:00` to next-day `09:00`, invokes manual scan, and renders model-first cards. Cards must show nearest department, distance, list price label, native groups, book/candidate status, personal state and change badges. Detail shows department history and separate manual annotations. History shows request conditions, counts and failure records. Admin allows city/probe CRUD, schedule enable toggle, interval/cron editing, and shows the fixed 60-day retention rule. Use responsive HTML/CSS and make no map a required workflow.

- [ ] **Step 4: Run page and API verification**

Run: `pytest tests/test_web_pages.py tests/test_routes.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/web.py app/templates app/static app/main.py tests/test_web_pages.py
git commit -m "feat: add personal radar web interface"
```

### Task 8: Add retention execution, deployment checks, and end-to-end acceptance

**Files:**
- Create: `app/maintenance.py`
- Create: `scripts/run_acceptance_scan.py`
- Create: `tests/test_maintenance.py`
- Create: `tests/test_end_to_end.py`
- Modify: `app/scheduler.py`
- Modify: `README.md`
- Modify: `.env.example`

**Interfaces:**
- Produces `RawPayloadMaintenance.purge(now: datetime) -> int` and a daily maintenance job.
- Produces `python scripts/run_acceptance_scan.py --lat 23.1291 --lon 113.2644 --pickup 2026-09-05T09:00:00+08:00 --return 2026-09-06T09:00:00+08:00`.

- [ ] **Step 1: Write failing retention and end-to-end tests**

```python
def test_daily_maintenance_removes_raw_payload_on_day_61(session):
    removed = RawPayloadMaintenance(session).purge(datetime(2026, 8, 30, tzinfo=UTC))
    assert removed == 1
    assert ScanRepository(session).count_snapshots() == 1

def test_manual_scan_to_discovery_workflow(client, mocked_zuche_client):
    run = client.post("/api/scans", json=SCAN_BODY).json()
    page = client.get("/", params={"scan_id": run["id"]})
    assert "小鹏P7+" in page.text
```

- [ ] **Step 2: Run the tests to verify failure**

Run: `pytest tests/test_maintenance.py tests/test_end_to_end.py -v`

Expected: FAIL because maintenance and acceptance script do not exist.

- [ ] **Step 3: Implement retention and production-operability features**

Schedule a daily raw-payload cleanup with a 60-day cutoff. Add an explicit acceptance script that prints API outcome, department count, offer count, unique model-ID count and the saved scan ID; it must exit non-zero on a failed scan and must not log cookies or raw payloads. Document environment variables, PostgreSQL backup via `pg_dump`, Compose start/stop, migration command, 60-day raw-data rule, and how to leave all automatic probes disabled.

- [ ] **Step 4: Run full automated verification and a controlled real scan**

Run: `pytest -v && docker compose config -q`

Expected: all tests PASS and Compose validates.

Then run once against the live gateway with the already validated Guangzhou centre coordinate and the approved standard one-day window:

```bash
python scripts/run_acceptance_scan.py --lat 23.1291 --lon 113.2644 --pickup 2026-09-05T09:00:00+08:00 --return 2026-09-06T09:00:00+08:00
```

Expected: `SUCCESS`, non-zero department and offer counts, and a persisted scan ID. If the external API does not respond successfully, report the saved failure record without treating it as a local test failure.

- [ ] **Step 5: Commit**

```bash
git add app/maintenance.py app/scheduler.py scripts/run_acceptance_scan.py tests/test_maintenance.py tests/test_end_to_end.py README.md .env.example
git commit -m "feat: add retention and acceptance workflow"
```

## Plan self-review

### Spec coverage

- Personal-only, PostgreSQL, no booking/authentication, port 8093: Tasks 1–2.
- Validated gateway, city ID handling, raw source facts and list-price warning: Task 3 and Task 7.
- Manual scans, optional disabled-by-default schedules, failures, history and events: Tasks 4–5 and Task 8.
- 60-day raw payload retention with permanent normalized history: Tasks 2 and 8.
- Native filters plus derived descriptions and personal filters: Tasks 3, 5 and 6.
- Personal states and sourced trustworthy manual information: Task 6.
- Discovery, details, history, admin and 09:00 presets: Task 7.
- Guangzhou acceptance scan and future worker boundary: Tasks 4, 5 and 8.

### Placeholder and type review

No incomplete implementation marker, undefined function reference, or unspecified test instruction remains. `ScanRequest`, `ParsedScan`, `ScanRepository`, `ScanService`, `ZucheClient`, and API routes are introduced before later tasks consume them.
