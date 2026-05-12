import asyncio
import concurrent.futures
import json
import os
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - not expected in Linux container
    fcntl = None

import pandas as pd
from websockets import connect


WAZE_SD_TYPE_UID = "WAZE_JAM_LOCATION"
PRAGUE_TIMEZONE = "Europe/Prague"

class RiotIntegrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class AnalyticityCountKPIDefinition:
    kind: str
    label: str
    user_identifier: str
    jam_count_operator: str
    jam_count_value: int


def _build_analyticity_kpi_definitions() -> list[AnalyticityCountKPIDefinition]:
    return [
        AnalyticityCountKPIDefinition(
            kind="jam_1",
            label="Analyticity Waze Delay With Single Jam",
            user_identifier="analyticity_waze_delay_with_single_jam",
            jam_count_operator="eq",
            jam_count_value=1,
        ),
        AnalyticityCountKPIDefinition(
            kind="jam_2",
            label="Analyticity Waze Delay With Two Jams",
            user_identifier="analyticity_waze_delay_with_two_jams",
            jam_count_operator="eq",
            jam_count_value=2,
        ),
        AnalyticityCountKPIDefinition(
            kind="jam_3",
            label="Analyticity Waze Delay With Three Jams",
            user_identifier="analyticity_waze_delay_with_three_jams",
            jam_count_operator="eq",
            jam_count_value=3,
        ),
        AnalyticityCountKPIDefinition(
            kind="jam_over_3",
            label="Analyticity Waze Delay With More Than Three Jams",
            user_identifier="analyticity_waze_delay_with_more_than_three_jams",
            jam_count_operator="gt",
            jam_count_value=3,
        ),
    ]


class RiotWebSocketClient:
    def __init__(self) -> None:
        api_key = os.getenv("RIOT_API_KEY", "").strip()
        if not api_key:
            raise RiotIntegrationError("Missing required environment variable RIOT_API_KEY")

        self.ws_url = os.getenv("RIOT_WS_URL", "ws://localhost:9090/ws").strip()
        self.origin = os.getenv("RIOT_WS_ORIGIN", "http://localhost:8080").strip()
        self.headers = {"X-API-Key": api_key}

    def request(self, action: str, payload: Any = None) -> Any:
        return _run_coro_sync(self._request(action, payload))

    def stream_request(self, action: str, payload: Any = None) -> list[Any]:
        return _run_coro_sync(self._stream_request(action, payload))

    async def _request(self, action: str, payload: Any = None) -> Any:
        responses = await self._stream_request(action, payload, expect_stream=False)
        if not responses:
            return None
        return responses[-1]

    async def _stream_request(self, action: str, payload: Any = None, expect_stream: bool = True) -> list[Any]:
        message_id = str(uuid.uuid4())
        request: dict[str, Any] = {
            "type": "request",
            "id": message_id,
            "action": action,
        }
        if payload is not None:
            request["payload"] = payload

        async with connect(
            self.ws_url,
            extra_headers=self.headers,
            origin=self.origin,
            open_timeout=20,
            close_timeout=5,
            max_size=None,
        ) as websocket:
            await websocket.send(json.dumps(request))
            responses: list[Any] = []

            while True:
                raw_message = await websocket.recv()
                message = json.loads(raw_message)

                if message.get("id") != message_id or message.get("type") != "response":
                    continue

                if message.get("error"):
                    raise RiotIntegrationError(f"{action} failed: {message['error']}")

                payload_data = message.get("payload")
                responses.append(payload_data)

                if not expect_stream:
                    break

                if not isinstance(payload_data, dict) or payload_data.get("hasMoreBatches") is not True:
                    break

            return responses


class RiotAnalyticsService:
    def __init__(self) -> None:
        self.sd_type_uid = os.getenv("RIOT_WAZE_SD_TYPE_UID", WAZE_SD_TYPE_UID).strip()
        self.chunk_days = int(os.getenv("RIOT_QUERY_CHUNK_DAYS", "7"))
        self.kpi_lock_path = os.getenv("RIOT_KPI_BOOTSTRAP_LOCK_FILE", "/tmp/analyticity_riot_kpi_bootstrap.lock")
        self._client: RiotWebSocketClient | None = None

        self._sd_type_cache: dict[str, Any] | None = None
        self._analyticity_kpis_cache: dict[str, dict[str, Any]] | None = None
        self._availability_cache: dict[str, str] | None = None

    @property
    def client(self) -> RiotWebSocketClient:
        if self._client is None:
            self._client = RiotWebSocketClient()
        return self._client

    def get_distinct_streets(self) -> list[str]:
        sd_type = self.get_waze_sd_type()
        payload = {
            "type": "raw",
            "sdTypeID": sd_type["id"],
            "tag": "street",
        }
        response = self.client.request("time-series-distinct-tag-values", payload)
        values = response.get("values", []) if isinstance(response, dict) else []
        return sorted([value for value in values if value and value != "unknown"])

    def get_waze_sd_type(self) -> dict[str, Any]:
        if self._sd_type_cache is not None:
            return self._sd_type_cache

        sd_types = self.client.request("get_sd_types")
        if not isinstance(sd_types, list):
            raise RiotIntegrationError("Unexpected response for get_sd_types")

        target = next((item for item in sd_types if item.get("uid") == self.sd_type_uid), None)
        if target is None:
            raise RiotIntegrationError(f"RIoT SDType '{self.sd_type_uid}' was not found")

        self._sd_type_cache = target
        return target

    def ensure_analyticity_kpis(self) -> dict[str, dict[str, Any]]:
        if self._analyticity_kpis_cache is not None:
            return self._analyticity_kpis_cache

        with _file_lock(self.kpi_lock_path):
            sd_type = self.get_waze_sd_type()
            parameters = {item["denotation"]: item for item in sd_type.get("parameters", [])}
            delay_parameter = parameters.get("delay")
            if not delay_parameter:
                raise RiotIntegrationError("Waze SDType does not expose 'delay' parameter")
            jam_count_parameter = parameters.get("jamCount")
            if not jam_count_parameter:
                raise RiotIntegrationError("Waze SDType does not expose 'jamCount' parameter")

            definitions = self._load_kpi_definitions(sd_type["id"])
            resolved: dict[str, dict[str, Any]] = {}
            missing: list[AnalyticityCountKPIDefinition] = []

            for definition in _build_analyticity_kpi_definitions():
                existing = self._find_existing_analyticity_kpi(definitions, definition)
                if existing is None:
                    missing.append(definition)
                    continue
                resolved[definition.kind] = existing

            if missing:
                for definition in missing:
                    self.client.request(
                        "create_kpi_definition",
                        self._build_analyticity_kpi_input(sd_type, delay_parameter, jam_count_parameter, definition),
                    )

                definitions = self._load_kpi_definitions(sd_type["id"])
                for definition in missing:
                    existing = self._find_existing_analyticity_kpi(definitions, definition)
                    if existing is None:
                        raise RiotIntegrationError(
                            f"Failed to locate KPI '{definition.label}' after creation"
                        )
                    resolved[definition.kind] = existing

        self._analyticity_kpis_cache = resolved
        return resolved

    def _load_kpi_definitions(self, sd_type_id: int) -> list[dict[str, Any]]:
        definitions = self.client.request("get_kpi_definitions_by_type", sd_type_id)
        if not isinstance(definitions, list):
            raise RiotIntegrationError("Unexpected response for get_kpi_definitions_by_type")
        return definitions

    def _find_existing_analyticity_kpi(
        self,
        definitions: list[dict[str, Any]],
        definition: AnalyticityCountKPIDefinition,
    ) -> dict[str, Any] | None:
        matches = [
            item
            for item in definitions
            if item.get("label") == definition.label
            or item.get("userIdentifier") == definition.user_identifier
        ]
        if not matches:
            return None

        matches.sort(key=lambda item: int(item.get("id", 0)))
        return matches[0]

    def warm_up(self) -> None:
        self.get_waze_sd_type()
        self.ensure_analyticity_kpis()
        self.get_data_availability(force_refresh=True)

    def get_data_availability(self, force_refresh: bool = False) -> dict[str, str]:
        if self._availability_cache is not None and not force_refresh:
            return self._availability_cache

        sd_type = self.get_waze_sd_type()
        oldest = self._load_boundary_timestamp(sd_type["id"], sort_desc=False)
        newest = self._load_boundary_timestamp(sd_type["id"], sort_desc=True)

        if oldest is None or newest is None:
            now = datetime.now(timezone.utc)
            oldest = oldest or now
            newest = newest or now

        suggested_from = max(oldest, newest - timedelta(days=7))
        availability = {
            "oldestDate": oldest.astimezone().date().isoformat(),
            "newestDate": newest.astimezone().date().isoformat(),
            "suggestedFromDate": suggested_from.astimezone().date().isoformat(),
            "suggestedToDate": newest.astimezone().date().isoformat(),
        }
        self._availability_cache = availability
        return availability

    def clamp_date_range(self, from_date: str | None, to_date: str | None) -> tuple[datetime, datetime]:
        requested_from, requested_to = _normalize_range(from_date, to_date)
        availability = self.get_data_availability()

        oldest = pd.Timestamp(availability["oldestDate"]).to_pydatetime().replace(tzinfo=timezone.utc)
        newest_exclusive = (
            pd.Timestamp(availability["newestDate"]).to_pydatetime().replace(tzinfo=timezone.utc)
            + timedelta(days=1)
        )

        effective_from = max(requested_from, oldest)
        effective_to = min(requested_to, newest_exclusive)

        if effective_to <= effective_from:
            effective_from = oldest
            effective_to = newest_exclusive

        return effective_from, effective_to

    def load_jam_records(self, from_date: str | None, to_date: str | None, streets: list[str] | None = None) -> pd.DataFrame:
        sd_type = self.get_waze_sd_type()
        from_utc, to_utc = self.clamp_date_range(from_date, to_date)
        payload_template: dict[str, Any] = {
            "type": "raw",
            "sdTypeID": sd_type["id"],
            "batch": 2000,
            "sortDesc": False,
        }
        street_filter = _build_street_filter(streets)
        if street_filter is not None:
            payload_template["filters"] = street_filter

        records: list[dict[str, Any]] = []
        for chunk_from, chunk_to in _iter_chunks(from_utc, to_utc, self.chunk_days):
            payload = dict(payload_template)
            payload["from"] = chunk_from.isoformat().replace("+00:00", "Z")
            payload["to"] = chunk_to.isoformat().replace("+00:00", "Z")

            for batch in self.client.stream_request("time-series", payload):
                records.extend(_parse_time_series_batch(batch))

        return _records_to_dataframe(records)

    def load_street_severity(self, from_date: str | None, to_date: str | None, streets: list[str] | None = None) -> dict[str, str]:
        raw_records = self.load_jam_records(from_date, to_date, streets)
        from_utc, to_utc = self.clamp_date_range(from_date, to_date)
        kpis = self.ensure_analyticity_kpis()
        kpi_records = self._load_analyticity_kpi_records(from_utc, to_utc, streets, kpis)
        return self._derive_street_severity_from_analyticity_counts(raw_records, kpi_records, from_utc, to_utc)

    def _load_analyticity_kpi_records(
        self,
        from_utc: datetime,
        to_utc: datetime,
        streets: list[str] | None,
        kpis: dict[str, dict[str, Any]],
    ) -> pd.DataFrame:
        sd_type = self.get_waze_sd_type()
        payload_template: dict[str, Any] = {
            "type": "kpi",
            "sdTypeID": sd_type["id"],
            "kpiDefinitionIDs": [item["id"] for item in kpis.values()],
            "batch": 2000,
            "sortDesc": False,
        }
        street_filter = _build_street_filter(streets)
        if street_filter is not None:
            payload_template["filters"] = street_filter

        records: list[dict[str, Any]] = []
        for chunk_from, chunk_to in _iter_chunks(from_utc, to_utc, self.chunk_days):
            payload = dict(payload_template)
            payload["from"] = chunk_from.isoformat().replace("+00:00", "Z")
            payload["to"] = chunk_to.isoformat().replace("+00:00", "Z")

            for batch in self.client.stream_request("time-series", payload):
                records.extend(_parse_time_series_batch(batch))

        return _kpi_records_to_dataframe(records)

    def _derive_street_severity_from_analyticity_counts(
        self,
        raw_records: pd.DataFrame,
        kpi_records: pd.DataFrame,
        from_utc: datetime,
        to_utc: datetime,
    ) -> dict[str, str]:
        day_count = max(1, int((to_utc - from_utc).total_seconds() // 86400))
        all_streets = sorted(
            {
                *(raw_records["street"].tolist() if not raw_records.empty else []),
                *(kpi_records["street"].tolist() if not kpi_records.empty else []),
            }
        )
        all_streets = [street for street in all_streets if street]
        if not all_streets:
            return {}

        kpis = self.ensure_analyticity_kpis()
        exact_jam_kpi_ids = {
            int(kpis["jam_1"]["id"]): 1,
            int(kpis["jam_2"]["id"]): 2,
            int(kpis["jam_3"]["id"]): 3,
        }
        overflow_kpi_id = int(kpis["jam_over_3"]["id"])
        severity_by_street: dict[str, str] = {}

        for street in all_streets:
            street_raw = raw_records[raw_records["street"] == street].copy()
            street_kpis = kpi_records[kpi_records["street"] == street].copy()
            occurrence_count = _count_street_occurrences(
                street_raw,
                street_kpis,
                exact_jam_kpi_ids,
                overflow_kpi_id,
                _to_local_naive(from_utc),
                _to_local_naive(to_utc),
            )
            severity_by_street[street] = _color_for_occurrence_count(occurrence_count, day_count)

        return severity_by_street

    def _load_boundary_timestamp(self, sd_type_id: int, sort_desc: bool) -> datetime | None:
        payload = {
            "type": "raw",
            "sdTypeID": sd_type_id,
            "limit": 1,
            "batch": 1,
            "sortDesc": sort_desc,
        }
        response = self.client.request("time-series", payload)
        records = _parse_time_series_batch(response)
        if not records:
            return None

        timestamp = records[0].get("time")
        if not timestamp:
            return None

        return pd.Timestamp(timestamp).to_pydatetime().astimezone(timezone.utc)

    def _build_analyticity_kpi_input(
        self,
        sd_type: dict[str, Any],
        delay_parameter: dict[str, Any],
        jam_count_parameter: dict[str, Any],
        definition: AnalyticityCountKPIDefinition,
    ) -> dict[str, Any]:
        nodes = [
            {
                "id": 1,
                "type": "LogicalOperation",
                "logicalOperationType": "and",
            },
            {
                "id": 2,
                "parentNodeID": 1,
                "type": "NumericNEQAtom",
                "sdParameterID": delay_parameter["id"],
                "sdParameterSpecification": delay_parameter["denotation"],
                "numericReferenceValue": 0.0,
            },
            {
                "id": 3,
                "parentNodeID": 1,
                "type": "NumericEQAtom" if definition.jam_count_operator == "eq" else "NumericGTAtom",
                "sdParameterID": jam_count_parameter["id"],
                "sdParameterSpecification": jam_count_parameter["denotation"],
                "numericReferenceValue": definition.jam_count_value,
            },
        ]

        return {
            "label": definition.label,
            "sdTypeID": sd_type["id"],
            "sdTypeUID": sd_type["uid"],
            "userIdentifier": definition.user_identifier,
            "sdInstanceMode": "all",
            "selectedSDInstanceIDs": [],
            "nodes": nodes,
        }


def _normalize_range(from_date: str | None, to_date: str | None) -> tuple[datetime, datetime]:
    now_utc = datetime.now(timezone.utc)
    start = pd.Timestamp(from_date or (now_utc - timedelta(days=7)).date()).to_pydatetime()
    end = pd.Timestamp(to_date or now_utc.date()).to_pydatetime()

    if end.hour == 0 and end.minute == 0 and end.second == 0 and end.microsecond == 0:
        end = end + timedelta(days=1)

    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    else:
        start = start.astimezone(timezone.utc)

    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    else:
        end = end.astimezone(timezone.utc)

    if end <= start:
        end = start + timedelta(days=1)

    return start, end


def _iter_chunks(start: datetime, end: datetime, chunk_days: int):
    current = start
    while current < end:
        chunk_end = min(current + timedelta(days=chunk_days), end)
        yield current, chunk_end
        current = chunk_end


def _build_street_filter(streets: list[str] | None) -> dict[str, Any] | None:
    unique_streets = sorted({street for street in (streets or []) if street})
    if not unique_streets:
        return None

    return {
        "type": "rule",
        "rule": {
            "tag": "street",
            "operator": "in",
            "value": ",".join(unique_streets),
        },
    }


def _color_for_occurrence_count(occurrence_count: int, day_count: int) -> str:
    if occurrence_count < day_count * 3:
        return "green"
    if occurrence_count < day_count * 7:
        return "orange"
    return "red"


def _run_coro_sync(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(asyncio.run, coro)
        return future.result()


@contextmanager
def _file_lock(lock_path: str):
    if fcntl is None:
        yield
        return

    with open(lock_path, "w", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _parse_time_series_batch(batch: Any) -> list[dict[str, Any]]:
    if not isinstance(batch, dict):
        return []

    records: list[dict[str, Any]] = []
    for point in batch.get("data", []) or []:
        tags_raw = point.get("tags") or "{}"
        data_raw = point.get("data") or "{}"

        tags = json.loads(tags_raw) if isinstance(tags_raw, str) else tags_raw
        data = json.loads(data_raw) if isinstance(data_raw, str) else data_raw

        records.append(
            {
                "time": point.get("time"),
                "tags": tags or {},
                "data": data or {},
            }
        )

    return records


def _records_to_dataframe(records: list[dict[str, Any]]) -> pd.DataFrame:
    if not records:
        return pd.DataFrame(
            columns=["pubMillis", "street", "segmentId", "delay", "length", "level", "speedKMH", "jamCount", "rawJams"]
        )

    rows: list[dict[str, Any]] = []
    for record in records:
        tags = record.get("tags", {})
        data = record.get("data", {})
        rows.append(
            {
                "pubMillis": record.get("time"),
                "street": tags.get("street"),
                "segmentId": tags.get("segmentId"),
                "delay": float(data.get("delay", 0) or 0),
                "length": float(data.get("length", 0) or 0),
                "level": float(data.get("level", 0) or 0),
                "speedKMH": float(data.get("speedKPH", data.get("speedKMH", 0)) or 0),
                "jamCount": int(float(data.get("jamCount", 0) or 0)),
                "rawJams": _parse_raw_jams(data.get("rawJams")),
            }
        )

    frame = pd.DataFrame(rows)
    frame["pubMillis"] = (
        pd.to_datetime(frame["pubMillis"], utc=True, format="mixed")
        .dt.tz_convert(PRAGUE_TIMEZONE)
        .dt.tz_localize(None)
    )
    frame["street"] = frame["street"].fillna("")
    frame = frame[frame["street"].str.len() > 0]
    return frame


def _kpi_records_to_dataframe(records: list[dict[str, Any]]) -> pd.DataFrame:
    if not records:
        return pd.DataFrame(columns=["pubMillis", "street", "kpiDefinitionID", "fulfilled"])

    rows: list[dict[str, Any]] = []
    for record in records:
        tags = record.get("tags", {})
        data = record.get("data", {})
        kpi_id = tags.get("kpiDefinitionID")
        if kpi_id is None:
            continue
        try:
            kpi_definition_id = int(kpi_id)
        except (TypeError, ValueError):
            continue
        rows.append(
            {
                "pubMillis": _to_local_naive(pd.Timestamp(record.get("time")).to_pydatetime().astimezone(timezone.utc)),
                "street": tags.get("street") or "",
                "kpiDefinitionID": kpi_definition_id,
                "fulfilled": bool(data.get("fulfilled")),
            }
        )

    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=["pubMillis", "street", "kpiDefinitionID", "fulfilled"])
    frame = frame[frame["street"].str.len() > 0]
    return frame.sort_values(by=["street", "pubMillis", "kpiDefinitionID"]).reset_index(drop=True)


def _parse_raw_jams(raw_value: Any) -> list[dict[str, Any]]:
    if isinstance(raw_value, list):
        return [item for item in raw_value if isinstance(item, dict)]
    if isinstance(raw_value, str) and raw_value.strip():
        try:
            decoded = json.loads(raw_value)
        except json.JSONDecodeError:
            return []
        if isinstance(decoded, list):
            return [item for item in decoded if isinstance(item, dict)]
    return []


def _to_local_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.astimezone(pd.Timestamp.now(tz=PRAGUE_TIMEZONE).tz).replace(tzinfo=None)


def _count_street_occurrences(
    street_raw: pd.DataFrame,
    street_kpis: pd.DataFrame,
    exact_jam_kpi_ids: dict[int, int],
    overflow_kpi_id: int,
    range_start: datetime,
    range_end: datetime,
) -> int:
    if street_kpis.empty:
        return _count_street_occurrences_from_raw(street_raw)

    street_kpis = street_kpis.sort_values(by=["pubMillis", "kpiDefinitionID"]).reset_index(drop=True)
    return _count_street_occurrences_from_snapshots(
        _build_street_snapshots(street_raw, street_kpis, exact_jam_kpi_ids, overflow_kpi_id, range_start, range_end)
    )


def _count_street_occurrences_from_raw(street_raw: pd.DataFrame) -> int:
    return _count_street_occurrences_from_snapshots(
        _build_street_snapshots_from_raw(street_raw)
    )


def _build_street_snapshots(
    street_raw: pd.DataFrame,
    street_kpis: pd.DataFrame,
    exact_jam_kpi_ids: dict[int, int],
    overflow_kpi_id: int,
    range_start: datetime,
    range_end: datetime,
) -> list[dict[str, Any]]:
    raw_snapshots = {
        snapshot["pubMillis"]: snapshot
        for snapshot in _build_street_snapshots_from_raw(street_raw)
    }

    if street_kpis.empty:
        return [raw_snapshots[key] for key in sorted(raw_snapshots)]

    kpi_state = {kpi_id: False for kpi_id in exact_jam_kpi_ids}
    kpi_state[overflow_kpi_id] = False
    snapshots: dict[datetime, dict[str, Any]] = dict(raw_snapshots)

    for row in street_kpis.itertuples(index=False):
        if row.pubMillis < range_start or row.pubMillis >= range_end:
            continue
        if row.kpiDefinitionID not in kpi_state:
            continue

        kpi_state[row.kpiDefinitionID] = bool(row.fulfilled)
        snapshot = snapshots.get(row.pubMillis, {
            "pubMillis": row.pubMillis,
            "delay_active": False,
            "jam_count": 0,
            "jam_ids": set(),
        })
        if snapshot["jam_count"] <= 0:
            snapshot["jam_count"] = _jam_count_from_kpi_state(kpi_state, exact_jam_kpi_ids, overflow_kpi_id)
        snapshot["delay_active"] = snapshot["delay_active"] or snapshot["jam_count"] > 0
        snapshots[row.pubMillis] = snapshot

    return [snapshots[key] for key in sorted(snapshots)]


def _build_street_snapshots_from_raw(street_raw: pd.DataFrame) -> list[dict[str, Any]]:
    if street_raw.empty:
        return []

    snapshots: list[dict[str, Any]] = []
    for pub_millis, group in street_raw.groupby("pubMillis", sort=True):
        delay_active = bool((group["delay"] != 0).any())
        jam_ids: set[str] = set()
        for raw_jams in group["rawJams"]:
            jam_ids.update(_extract_raw_jam_ids(raw_jams))

        jam_count = len(jam_ids)
        if jam_count == 0:
            jam_count = int(group["jamCount"].max() or 0)

        snapshots.append(
            {
                "pubMillis": pub_millis,
                "delay_active": delay_active,
                "jam_count": jam_count if delay_active else 0,
                "jam_ids": jam_ids if delay_active else set(),
            }
        )

    return snapshots


def _jam_count_from_kpi_state(
    kpi_state: dict[int, bool],
    exact_jam_kpi_ids: dict[int, int],
    overflow_kpi_id: int,
) -> int:
    active_exact = [jam_count for kpi_id, jam_count in exact_jam_kpi_ids.items() if kpi_state.get(kpi_id)]
    if active_exact:
        return max(active_exact)
    if kpi_state.get(overflow_kpi_id):
        return 4
    return 0


def _count_street_occurrences_from_snapshots(snapshots: list[dict[str, Any]]) -> int:
    if not snapshots:
        return 0

    total = 0
    previous_single_active = False
    previous_multi_ids: set[str] = set()
    previous_multi_count = 0

    for snapshot in sorted(snapshots, key=lambda item: item["pubMillis"]):
        delay_active = bool(snapshot.get("delay_active"))
        jam_count = int(snapshot.get("jam_count") or 0)
        jam_ids = set(snapshot.get("jam_ids") or set())

        if not delay_active or jam_count <= 0:
            previous_single_active = False
            previous_multi_ids = set()
            previous_multi_count = 0
            continue

        if jam_count == 1:
            if not previous_single_active:
                total += 1
            previous_single_active = True
            previous_multi_ids = set()
            previous_multi_count = 0
            continue

        previous_single_active = False

        if jam_ids:
            if not previous_multi_ids:
                total += len(jam_ids)
            else:
                total += len(jam_ids - previous_multi_ids)
            previous_multi_ids = jam_ids
            previous_multi_count = len(jam_ids)
            continue

        if previous_multi_count == 0:
            total += jam_count
        elif jam_count > previous_multi_count:
            total += jam_count - previous_multi_count
        previous_multi_count = jam_count

    return total


def _extract_raw_jam_ids(raw_jams: list[dict[str, Any]]) -> set[str]:
    ids: set[str] = set()
    for item in raw_jams:
        candidate = item.get("uuid") or item.get("jam_uuid") or item.get("id")
        if candidate:
            ids.add(str(candidate))
            continue
        ids.add(json.dumps(item, sort_keys=True, ensure_ascii=True))
    return ids


riot_service = RiotAnalyticsService()
