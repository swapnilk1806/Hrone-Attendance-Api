"""Employee Attendance & Analytics API (FastAPI + MongoDB)."""
from __future__ import annotations

import os
import re
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Literal
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)
from pymongo import ASCENDING, DESCENDING, MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "attendance_db")

IST = ZoneInfo("Asia/Kolkata")
PRESENCE = ["PRESENT", "WFH", "ON_DUTY"]
NON_PRESENCE = ["ABSENT", "LEAVE"]
ALL_STATUSES = PRESENCE + NON_PRESENCE

client = MongoClient(
    MONGO_URI,
    tz_aware=False,
    serverSelectionTimeoutMS=5000,
)


def get_db():
    return client[MONGO_DB]


@asynccontextmanager
async def lifespan(_: FastAPI):
    ensure_indexes()
    yield
    client.close()


app = FastAPI(
    title="Employee Attendance & Analytics API",
    version="2.0.0",
    lifespan=lifespan,
)


# --------------------------------------------------
# Request models
# --------------------------------------------------

class EmployeeCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    emp_code: str = Field(..., pattern=r"^EMP\d{4,6}$")
    name: str = Field(..., min_length=1, max_length=100)
    email: str = Field(
        ...,
        max_length=120,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    )
    department: str = Field(..., min_length=1, max_length=50)
    shift_start: str = Field(
        "09:30",
        pattern=r"^([01]\d|2[0-3]):[0-5]\d$",
    )
    shift_end: str = Field(
        "18:30",
        pattern=r"^([01]\d|2[0-3]):[0-5]\d$",
    )
    joined_on: str

    @field_validator("joined_on")
    @classmethod
    def valid_joined_on(cls, value: str) -> str:
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(
                "joined_on must be YYYY-MM-DD"
            ) from exc

        if len(value) != 10:
            raise ValueError("joined_on must be YYYY-MM-DD")

        return value

    @model_validator(mode="after")
    def valid_shift(self):
        if self.shift_start == self.shift_end:
            raise ValueError(
                "shift_start and shift_end must differ"
            )
        return self


class PunchInRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    emp_code: str = Field(..., min_length=1)
    punched_at: StrictInt | None = None
    status: Literal["PRESENT", "WFH", "ON_DUTY"] = "PRESENT"

    @field_validator("punched_at")
    @classmethod
    def valid_time(cls, value):
        if value is not None:
            validate_epoch_millis(value)
        return value


class PunchOutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    emp_code: str = Field(..., min_length=1)
    punched_at: StrictInt | None = None

    @field_validator("punched_at")
    @classmethod
    def valid_time(cls, value):
        if value is not None:
            validate_epoch_millis(value)
        return value


class RegularizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal[
        "PRESENT",
        "WFH",
        "ON_DUTY",
        "ABSENT",
        "LEAVE",
    ] | None = None

    punch_in: StrictInt | None = None
    punch_out: StrictInt | None = None
    reason: str = Field(..., min_length=5, max_length=200)
    regularized_by: str = Field(..., min_length=1, max_length=50)

    @field_validator("punch_in", "punch_out")
    @classmethod
    def valid_time(cls, value):
        if value is not None:
            validate_epoch_millis(value)
        return value


# --------------------------------------------------
# Date and time helpers
# --------------------------------------------------

def validate_epoch_millis(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("Epoch milliseconds must be an integer")

    if not 100_000_000_000 <= value <= 4_102_444_800_000:
        raise ValueError("Epoch milliseconds out of range")


def ms_to_dt(value: int | None) -> datetime | None:
    if value is None:
        return None

    return datetime.fromtimestamp(
        value / 1000,
        timezone.utc,
    ).replace(tzinfo=None)


def dt_to_ms(value: datetime | None) -> int | None:
    if value is None:
        return None

    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)

    return int(
        value.replace(tzinfo=timezone.utc).timestamp() * 1000
    )


def truncate_ms(value: int) -> int:
    return (value // 1000) * 1000


def parse_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)

        if len(value) != 10:
            raise ValueError

        return parsed

    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail="Date must be a valid YYYY-MM-DD date",
        ) from exc


def parse_month(value: str) -> tuple[date, date]:
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value):
        raise HTTPException(
            status_code=422,
            detail="month must be YYYY-MM",
        )

    start = date.fromisoformat(value + "-01")

    if start.month == 12:
        end = date(start.year + 1, 1, 1)
    else:
        end = date(start.year, start.month + 1, 1)

    return start, end


def round_half_up(
    value: Decimal | float | int,
    places: int = 2,
) -> Decimal:
    quantum = Decimal("1").scaleb(-places)

    return Decimal(str(value)).quantize(
        quantum,
        rounding=ROUND_HALF_UP,
    )


def utc_shift_time(
    employee: dict[str, Any],
    record_date: str,
    field: str,
) -> datetime:
    current_date = date.fromisoformat(record_date)
    hour, minute = map(
        int,
        employee[field].split(":"),
    )

    if field == "shift_end":
        start_hour, start_minute = map(
            int,
            employee["shift_start"].split(":"),
        )

        if (
            hour * 60 + minute
            <= start_hour * 60 + start_minute
        ):
            current_date += timedelta(days=1)

    local_dt = datetime.combine(
        current_date,
        time(hour, minute),
        IST,
    )

    return local_dt.astimezone(
        timezone.utc
    ).replace(tzinfo=None)


def attendance_date_for_punch(
    employee: dict[str, Any],
    punch_utc: datetime,
) -> str:
    local = punch_utc.replace(
        tzinfo=timezone.utc
    ).astimezone(IST)

    start_hour, start_minute = map(
        int,
        employee["shift_start"].split(":"),
    )
    end_hour, end_minute = map(
        int,
        employee["shift_end"].split(":"),
    )

    if (
        end_hour * 60 + end_minute
        <= start_hour * 60 + start_minute
        and local.time().replace(tzinfo=None)
        < time(end_hour, end_minute)
    ):
        return (local.date() - timedelta(days=1)).isoformat()

    return local.date().isoformat()


def late_minutes(
    employee: dict[str, Any],
    record_date: str,
    punch_in: datetime,
) -> int:
    shift_start = utc_shift_time(
        employee,
        record_date,
        "shift_start",
    )

    delta = (punch_in - shift_start).total_seconds()

    if delta <= 600:
        return 0

    return int(delta // 60)


def derived_fields(
    employee: dict[str, Any],
    record_date: str,
    record_status: str,
    punch_in: datetime | None,
    punch_out: datetime | None,
) -> dict[str, Any]:
    if record_status in NON_PRESENCE or punch_in is None:
        return {
            "late_minutes": 0,
            "work_hours": None,
            "overtime_minutes": 0,
            "half_day": False,
        }

    late = late_minutes(
        employee,
        record_date,
        punch_in,
    )

    work_hours = None
    overtime = 0
    half_day = False

    if punch_out is not None:
        seconds = (punch_out - punch_in).total_seconds()

        work_hours = float(
            round_half_up(
                Decimal(str(seconds)) / Decimal(3600),
                2,
            )
        )

        shift_end = utc_shift_time(
            employee,
            record_date,
            "shift_end",
        )

        extra_seconds = max(
            0,
            (punch_out - shift_end).total_seconds(),
        )

        extra_minutes = int(extra_seconds // 60)

        if extra_minutes >= 30:
            overtime = extra_minutes

        half_day = work_hours < 4.5

    return {
        "late_minutes": late,
        "work_hours": work_hours,
        "overtime_minutes": overtime,
        "half_day": half_day,
    }


# --------------------------------------------------
# MongoDB indexes
# --------------------------------------------------

def ensure_indexes():
    db = get_db()

    db.employees.create_index(
        [("emp_code", ASCENDING)],
        unique=True,
        name="ix_employees_emp_code",
    )

    db.employees.create_index(
        [
            ("department", ASCENDING),
            ("joined_on", ASCENDING),
        ],
        name="ix_employees_department_joined_on",
    )

    db.attendance_logs.create_index(
        [
            ("emp_code", ASCENDING),
            ("date", ASCENDING),
        ],
        unique=True,
        name="ix_attendance_emp_code_date",
    )

    db.attendance_logs.create_index(
        [
            ("date", DESCENDING),
            ("emp_code", ASCENDING),
        ],
        name="ix_attendance_date_emp_code",
    )

    db.attendance_logs.create_index(
        [
            ("emp_code", ASCENDING),
            ("date", ASCENDING),
            ("status", ASCENDING),
        ],
        name="ix_attendance_emp_date_status",
    )

    db.attendance_logs.create_index(
        [
            ("date", ASCENDING),
            ("late_minutes", DESCENDING),
            ("emp_code", ASCENDING),
        ],
        name="ix_attendance_date_late_emp",
    )


# --------------------------------------------------
# Response serialization
# --------------------------------------------------

def employee_out(document: dict[str, Any]) -> dict[str, Any]:
    return {
        "emp_code": document["emp_code"],
        "name": document["name"],
        "email": document["email"],
        "department": document["department"],
        "shift_start": document.get("shift_start", "09:30"),
        "shift_end": document.get("shift_end", "18:30"),
        "joined_on": document["joined_on"],
        "created_at": dt_to_ms(document.get("created_at")),
    }


def record_out(document: dict[str, Any]) -> dict[str, Any]:
    history = []

    for entry in document.get("history", []) or []:
        changes = {}

        for field_name, values in (
            entry.get("changes") or {}
        ).items():
            if isinstance(values, dict):
                changes[field_name] = {
                    "from": (
                        dt_to_ms(values.get("from"))
                        if field_name in ("punch_in", "punch_out")
                        else values.get("from")
                    ),
                    "to": (
                        dt_to_ms(values.get("to"))
                        if field_name in ("punch_in", "punch_out")
                        else values.get("to")
                    ),
                }

        history.append({
            "at": dt_to_ms(entry.get("at")),
            "by": entry.get("by"),
            "reason": entry.get("reason"),
            "changes": changes,
        })

    return {
        "emp_code": document["emp_code"],
        "date": document["date"],
        "status": document["status"],
        "punch_in": dt_to_ms(document.get("punch_in")),
        "punch_out": dt_to_ms(document.get("punch_out")),
        "work_hours": document.get("work_hours"),
        "late_minutes": int(document.get("late_minutes", 0) or 0),
        "overtime_minutes": int(
            document.get("overtime_minutes", 0) or 0
        ),
        "half_day": bool(document.get("half_day", False)),
        "history": history,
    }


def month_bounds_strings(month: str):
    start, end = parse_month(month)

    return (
        start.isoformat(),
        (end - timedelta(days=1)).isoformat(),
        start,
        end,
    )


# --------------------------------------------------
# Health and employee endpoints
# --------------------------------------------------

@app.get("/health")
def health():
    try:
        get_db().command("ping")
    except PyMongoError as exc:
        raise HTTPException(
            status_code=503,
            detail="MongoDB unavailable",
        ) from exc

    return {"status": "ok"}


@app.post("/employees", status_code=201)
def create_employee(payload: EmployeeCreateRequest):
    db = get_db()

    document = payload.model_dump()
    document["created_at"] = datetime.now(
        timezone.utc
    ).replace(tzinfo=None)

    try:
        db.employees.insert_one(document)
    except DuplicateKeyError as exc:
        raise HTTPException(
            status_code=409,
            detail="Employee already exists",
        ) from exc

    return employee_out(document)


@app.get("/employees")
def list_employees(
    department: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    db = get_db()

    query = (
        {"department": department}
        if department is not None
        else {}
    )

    total = db.employees.count_documents(query)

    rows = (
        db.employees.find(query)
        .sort("emp_code", ASCENDING)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )

    return {
        "items": [employee_out(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


# --------------------------------------------------
# Attendance endpoints
# --------------------------------------------------

@app.post("/attendance/punch-in", status_code=201)
def punch_in(payload: PunchInRequest):
    db = get_db()

    employee = db.employees.find_one({
        "emp_code": payload.emp_code,
    })

    if not employee:
        raise HTTPException(
            status_code=404,
            detail="Employee not found",
        )

    timestamp = (
        payload.punched_at
        if payload.punched_at is not None
        else int(datetime.now(timezone.utc).timestamp() * 1000)
    )

    timestamp = truncate_ms(timestamp)
    punch_dt = ms_to_dt(timestamp)
    record_date = attendance_date_for_punch(
        employee,
        punch_dt,
    )

    document = {
        "emp_code": payload.emp_code,
        "date": record_date,
        "status": payload.status,
        "punch_in": punch_dt,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": late_minutes(
            employee,
            record_date,
            punch_dt,
        ),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    try:
        db.attendance_logs.insert_one(document)
    except DuplicateKeyError as exc:
        raise HTTPException(
            status_code=409,
            detail="Attendance already punched in for the date",
        ) from exc

    return record_out(document)


@app.post("/attendance/punch-out")
def punch_out(payload: PunchOutRequest):
    db = get_db()

    employee = db.employees.find_one({
        "emp_code": payload.emp_code,
    })

    if not employee:
        raise HTTPException(
            status_code=404,
            detail="Employee not found",
        )

    timestamp = (
        payload.punched_at
        if payload.punched_at is not None
        else int(datetime.now(timezone.utc).timestamp() * 1000)
    )

    timestamp = truncate_ms(timestamp)
    punch_out_dt = ms_to_dt(timestamp)

    record = db.attendance_logs.find_one(
        {
            "emp_code": payload.emp_code,
            "punch_in": {"$lte": punch_out_dt},
        },
        sort=[("punch_in", DESCENDING)],
    )

    if not record:
        raise HTTPException(
            status_code=404,
            detail="No punch-in found",
        )

    if record.get("punch_out") is not None:
        raise HTTPException(
            status_code=409,
            detail="Record already punched out",
        )

    if punch_out_dt <= record["punch_in"]:
        raise HTTPException(
            status_code=422,
            detail="punch_out must be after punch_in",
        )

    if punch_out_dt - record["punch_in"] > timedelta(hours=24):
        raise HTTPException(
            status_code=422,
            detail="punch_out exceeds 24 hours after punch_in",
        )

    calculated = derived_fields(
        employee,
        record["date"],
        record["status"],
        record["punch_in"],
        punch_out_dt,
    )

    updated = db.attendance_logs.find_one_and_update(
        {
            "_id": record["_id"],
            "punch_out": None,
        },
        {
            "$set": {
                "punch_out": punch_out_dt,
                **calculated,
            }
        },
        return_document=ReturnDocument.AFTER,
    )

    if updated is None:
        raise HTTPException(
            status_code=409,
            detail="Record already punched out",
        )

    return record_out(updated)


@app.patch("/attendance/{emp_code}/{date_text}")
def regularize_attendance(
    emp_code: str,
    date_text: str,
    payload: RegularizeRequest,
):
    parse_date(date_text)

    db = get_db()

    employee = db.employees.find_one({
        "emp_code": emp_code,
    })

    if not employee:
        raise HTTPException(
            status_code=404,
            detail="Employee not found",
        )

    record = db.attendance_logs.find_one({
        "emp_code": emp_code,
        "date": date_text,
    })

    if not record:
        raise HTTPException(
            status_code=404,
            detail="Attendance record not found",
        )

    new_status = (
        payload.status
        if payload.status is not None
        else record["status"]
    )

    if new_status in NON_PRESENCE and (
        payload.punch_in is not None
        or payload.punch_out is not None
    ):
        raise HTTPException(
            status_code=422,
            detail="ABSENT and LEAVE records must clear punch times",
        )

    punch_in_dt = (
        record.get("punch_in")
        if payload.punch_in is None
        else ms_to_dt(truncate_ms(payload.punch_in))
    )

    punch_out_dt = (
        record.get("punch_out")
        if payload.punch_out is None
        else ms_to_dt(truncate_ms(payload.punch_out))
    )

    if new_status in NON_PRESENCE:
        punch_in_dt = None
        punch_out_dt = None
    elif punch_in_dt is None:
        raise HTTPException(
            status_code=422,
            detail="Presence records require punch_in",
        )

    if (
        punch_in_dt is not None
        and attendance_date_for_punch(
            employee,
            punch_in_dt,
        ) != date_text
    ):
        raise HTTPException(
            status_code=422,
            detail="punch_in must fall on the attendance date",
        )

    if punch_out_dt is not None and (
        punch_in_dt is None or punch_out_dt <= punch_in_dt
    ):
        raise HTTPException(
            status_code=422,
            detail="punch_out must be after punch_in",
        )

    if (
        punch_in_dt is not None
        and punch_out_dt is not None
        and punch_out_dt - punch_in_dt > timedelta(hours=24)
    ):
        raise HTTPException(
            status_code=422,
            detail="punch_out exceeds 24 hours after punch_in",
        )

    calculated = derived_fields(
        employee,
        date_text,
        new_status,
        punch_in_dt,
        punch_out_dt,
    )

    updated_fields = {
        "status": new_status,
        "punch_in": punch_in_dt,
        "punch_out": punch_out_dt,
        **calculated,
    }

    keys = [
        "status",
        "punch_in",
        "punch_out",
        "work_hours",
        "late_minutes",
        "overtime_minutes",
        "half_day",
    ]

    changes = {}

    for key in keys:
        old_value = record.get(key)
        new_value = updated_fields.get(key)

        if old_value != new_value:
            changes[key] = {
                "from": (
                    dt_to_ms(old_value)
                    if key in ("punch_in", "punch_out")
                    else old_value
                ),
                "to": (
                    dt_to_ms(new_value)
                    if key in ("punch_in", "punch_out")
                    else new_value
                ),
            }

    if not changes:
        raise HTTPException(
            status_code=422,
            detail="No actual change supplied",
        )

    history_entry = {
        "at": datetime.now(timezone.utc).replace(tzinfo=None),
        "by": payload.regularized_by,
        "reason": payload.reason,
        "changes": changes,
    }

    guard = {
        "_id": record["_id"],
        "status": record["status"],
        "punch_in": record.get("punch_in"),
        "punch_out": record.get("punch_out"),
        "work_hours": record.get("work_hours"),
    }

    updated = db.attendance_logs.find_one_and_update(
        guard,
        {
            "$set": updated_fields,
            "$push": {"history": history_entry},
        },
        return_document=ReturnDocument.AFTER,
    )

    if updated is None:
        raise HTTPException(
            status_code=409,
            detail="Attendance record changed concurrently; retry regularization",
        )

    return record_out(updated)


@app.get("/attendance")
def list_attendance(
    emp_code: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    status_filter: str | None = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    db = get_db()
    query = {}

    if emp_code:
        query["emp_code"] = emp_code

    if date_from:
        parse_date(date_from)

    if date_to:
        parse_date(date_to)

    if date_from and date_to and date_from > date_to:
        raise HTTPException(
            status_code=422,
            detail="date_from cannot be greater than date_to",
        )

    if date_from or date_to:
        query["date"] = {}

        if date_from:
            query["date"]["$gte"] = date_from

        if date_to:
            query["date"]["$lte"] = date_to

    if status_filter:
        if status_filter not in ALL_STATUSES:
            raise HTTPException(
                status_code=422,
                detail="Invalid status",
            )

        query["status"] = status_filter

    total = db.attendance_logs.count_documents(query)

    rows = (
        db.attendance_logs.find(query)
        .sort([
            ("date", DESCENDING),
            ("emp_code", ASCENDING),
        ])
        .skip((page - 1) * page_size)
        .limit(page_size)
    )

    return {
        "items": [record_out(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


# --------------------------------------------------
# Employee monthly analytics
# --------------------------------------------------

@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(
    emp_code: str,
    month: str = Query(
        ...,
        pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
    ),
):
    db = get_db()

    employee = db.employees.find_one({
        "emp_code": emp_code,
    })

    if not employee:
        raise HTTPException(
            status_code=404,
            detail="Employee not found",
        )

    start, end, start_date, end_date = month_bounds_strings(month)

    pipeline = [
        {
            "$match": {
                "emp_code": emp_code,
                "date": {
                    "$gte": start,
                    "$lt": end,
                },
            }
        },
        {
            "$addFields": {
                "_day": {
                    "$dayOfWeek": {
                        "$dateFromString": {
                            "dateString": {
                                "$concat": [
                                    "$date",
                                    "T00:00:00Z",
                                ]
                            }
                        }
                    }
                }
            }
        },
        {
            "$group": {
                "_id": None,
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$in": ["$status", PRESENCE]},
                                    {"$gte": ["$_day", 2]},
                                    {"$lte": ["$_day", 6]},
                                    {
                                        "$gte": [
                                            "$date",
                                            employee["joined_on"],
                                        ]
                                    },
                                ]
                            },
                            {
                                "$cond": [
                                    {"$eq": ["$half_day", True]},
                                    0.5,
                                    1,
                                ]
                            },
                            0,
                        ]
                    }
                },
                "leave_days": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$status", "LEAVE"]},
                            1,
                            0,
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {
                                "$gt": [
                                    {"$ifNull": ["$late_minutes", 0]},
                                    0,
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },
                "total_late_minutes": {
                    "$sum": {"$ifNull": ["$late_minutes", 0]}
                },
                "total_overtime_minutes": {
                    "$sum": {"$ifNull": ["$overtime_minutes", 0]}
                },
            }
        },
    ]

    results = list(
        db.attendance_logs.aggregate(
            pipeline,
            hint="ix_attendance_emp_code_date",
        )
    )

    data = results[0] if results else {}

    cursor = max(
        start_date,
        date.fromisoformat(employee["joined_on"]),
    )

    working_days = 0

    while cursor < end_date:
        if cursor.weekday() < 5:
            working_days += 1

        cursor += timedelta(days=1)

    present_days = float(data.get("present_days", 0))

    attendance_pct = (
        float(
            round_half_up(
                Decimal(str(present_days))
                * 100
                / Decimal(working_days),
                2,
            )
        )
        if working_days
        else None
    )

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": int(data.get("leave_days", 0)),
        "late_count": int(data.get("late_count", 0)),
        "total_late_minutes": int(
            data.get("total_late_minutes", 0)
        ),
        "total_overtime_minutes": int(
            data.get("total_overtime_minutes", 0)
        ),
        "attendance_pct": attendance_pct,
    }


# --------------------------------------------------
# Department summary analytics
# --------------------------------------------------

@app.get("/analytics/departments/summary")
def department_summary(
    month: str = Query(
        ...,
        pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
    ),
    department: str | None = None,
):
    db = get_db()

    start, end, start_date, end_date = month_bounds_strings(month)
    last_day = (end_date - timedelta(days=1)).isoformat()

    employee_match = {
        "joined_on": {"$lte": last_day},
    }

    if department is not None:
        employee_match["department"] = department

    pipeline = [
        {"$match": employee_match},
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"code": "$emp_code"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$emp_code", "$$code"]},
                                    {"$gte": ["$date", start]},
                                    {"$lt": ["$date", end]},
                                ]
                            }
                        }
                    }
                ],
                "as": "logs",
            }
        },
        {
            "$unwind": {
                "path": "$logs",
                "preserveNullAndEmptyArrays": True,
            }
        },
        {
            "$addFields": {
                "_dow": {
                    "$cond": [
                        {
                            "$ne": [
                                {"$ifNull": ["$logs.date", None]},
                                None,
                            ]
                        },
                        {
                            "$dayOfWeek": {
                                "$dateFromString": {
                                    "dateString": {
                                        "$concat": [
                                            "$logs.date",
                                            "T00:00:00Z",
                                        ]
                                    }
                                }
                            }
                        },
                        None,
                    ]
                }
            }
        },
        {
            "$group": {
                "_id": "$department",
                "headcount": {"$addToSet": "$emp_code"},
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$in": ["$logs.status", PRESENCE]},
                                    {"$gte": ["$_dow", 2]},
                                    {"$lte": ["$_dow", 6]},
                                ]
                            },
                            {
                                "$cond": [
                                    {"$eq": ["$logs.half_day", True]},
                                    0.5,
                                    1,
                                ]
                            },
                            0,
                        ]
                    }
                },
                "hours_sum": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$in": ["$logs.status", PRESENCE]},
                                    {
                                        "$ne": [
                                            {
                                                "$ifNull": [
                                                    "$logs.work_hours",
                                                    None,
                                                ]
                                            },
                                            None,
                                        ]
                                    },
                                ]
                            },
                            "$logs.work_hours",
                            0,
                        ]
                    }
                },
                "hours_count": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$in": ["$logs.status", PRESENCE]},
                                    {
                                        "$ne": [
                                            {
                                                "$ifNull": [
                                                    "$logs.work_hours",
                                                    None,
                                                ]
                                            },
                                            None,
                                        ]
                                    },
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {
                                "$gt": [
                                    {
                                        "$ifNull": [
                                            "$logs.late_minutes",
                                            0,
                                        ]
                                    },
                                    0,
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },
                "total_late_minutes": {
                    "$sum": {
                        "$ifNull": ["$logs.late_minutes", 0]
                    }
                },
                "leave_count": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$logs.status", "LEAVE"]},
                            1,
                            0,
                        ]
                    }
                },
                "on_duty_count": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$logs.status", "ON_DUTY"]},
                            1,
                            0,
                        ]
                    }
                },
            }
        },
        {
            "$project": {
                "_id": 0,
                "department": "$_id",
                "headcount": {"$size": "$headcount"},
                "present_days": 1,
                "avg_work_hours": {
                    "$cond": [
                        {"$gt": ["$hours_count", 0]},
                        {"$divide": ["$hours_sum", "$hours_count"]},
                        None,
                    ]
                },
                "late_count": 1,
                "total_late_minutes": 1,
                "leave_count": 1,
                "on_duty_count": 1,
            }
        },
        {"$sort": {"department": 1}},
    ]

    rows = list(
        db.employees.aggregate(
            pipeline,
            allowDiskUse=True,
        )
    )

    for row in rows:
        if row.get("avg_work_hours") is not None:
            row["avg_work_hours"] = float(
                round_half_up(row["avg_work_hours"], 2)
            )

        row["present_days"] = float(
            row.get("present_days", 0)
        )

    return {"month": month, "items": rows}


# --------------------------------------------------
# Late leaderboard
# --------------------------------------------------

@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str = Query(
        ...,
        pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
    ),
    limit: int = Query(10, ge=1, le=50),
    department: str | None = None,
):
    db = get_db()

    start, end, _, _ = month_bounds_strings(month)

    pipeline = [
        {
            "$match": {
                "date": {"$gte": start, "$lt": end},
                "late_minutes": {"$gt": 0},
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "localField": "emp_code",
                "foreignField": "emp_code",
                "as": "employee",
            }
        },
        {"$unwind": "$employee"},
    ]

    if department is not None:
        pipeline.append({
            "$match": {
                "employee.department": department,
            }
        })

    pipeline.extend([
        {
            "$group": {
                "_id": "$emp_code",
                "name": {"$first": "$employee.name"},
                "department": {"$first": "$employee.department"},
                "total_late_minutes": {"$sum": "$late_minutes"},
                "late_count": {"$sum": 1},
            }
        },
        {
            "$setWindowFields": {
                "sortBy": {
                    "total_late_minutes": -1,
                    "_id": 1,
                },
                "output": {
                    "rank": {"$rank": {}},
                },
            }
        },
        {"$match": {"rank": {"$lte": limit}}},
        {"$sort": {"rank": 1, "_id": 1}},
        {
            "$project": {
                "_id": 0,
                "rank": 1,
                "emp_code": "$_id",
                "name": 1,
                "department": 1,
                "total_late_minutes": 1,
                "late_count": 1,
            }
        },
    ])

    rows = list(
        db.attendance_logs.aggregate(
            pipeline,
            allowDiskUse=True,
        )
    )

    return {"month": month, "items": rows}


# --------------------------------------------------
# Department daily trend
# --------------------------------------------------

@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    from_: str = Query(..., alias="from"),
    to: str = Query(...),
):
    db = get_db()

    from_date = parse_date(from_)
    to_date = parse_date(to)

    if to_date < from_date:
        raise HTTPException(
            status_code=422,
            detail="to cannot be earlier than from",
        )

    if (to_date - from_date).days > 92:
        raise HTTPException(
            status_code=422,
            detail="Trend range exceeds 92 days",
        )

    employees = list(
        db.employees.find(
            {
                "department": department,
                "joined_on": {"$lte": to_date.isoformat()},
            },
            {
                "_id": 0,
                "emp_code": 1,
                "joined_on": 1,
            },
        )
    )

    if not employees:
        raise HTTPException(
            status_code=404,
            detail="Department not found",
        )

    employee_codes = [
        employee["emp_code"]
        for employee in employees
    ]

    start_dt = datetime.combine(
        from_date,
        time.min,
        timezone.utc,
    )

    end_dt = datetime.combine(
        to_date,
        time.min,
        timezone.utc,
    )

    pipeline = [
        {
            "$match": {
                "emp_code": {"$in": employee_codes},
                "date": {
                    "$gte": from_date.isoformat(),
                    "$lte": to_date.isoformat(),
                },
            }
        },
        {
            "$addFields": {
                "day": {
                    "$dateFromString": {
                        "dateString": {
                            "$concat": ["$date", "T00:00:00Z"]
                        }
                    }
                }
            }
        },
        {
            "$group": {
                "_id": "$day",
                "present_count": {
                    "$sum": {
                        "$cond": [
                            {"$in": ["$status", PRESENCE]},
                            {
                                "$cond": [
                                    {"$eq": ["$half_day", True]},
                                    0.5,
                                    1,
                                ]
                            },
                            0,
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {
                                "$gt": [
                                    {
                                        "$ifNull": [
                                            "$late_minutes",
                                            0,
                                        ]
                                    },
                                    0,
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },
            }
        },
        {
            "$densify": {
                "field": "_id",
                "range": {
                    "step": 1,
                    "unit": "day",
                    "bounds": [start_dt, end_dt],
                },
            }
        },
        {
            "$fill": {
                "output": {
                    "present_count": {"value": 0},
                    "late_count": {"value": 0},
                }
            }
        },
        {
            "$addFields": {
                "date": {
                    "$dateToString": {
                        "date": "$_id",
                        "format": "%Y-%m-%d",
                    }
                },
                "is_working_day": {
                    "$and": [
                        {
                            "$gte": [
                                {"$dayOfWeek": "$_id"},
                                2,
                            ]
                        },
                        {
                            "$lte": [
                                {"$dayOfWeek": "$_id"},
                                6,
                            ]
                        },
                    ]
                },
            }
        },
        {"$addFields": {"_joined": {"$literal": employees}}},
        {
            "$addFields": {
                "headcount": {
                    "$size": {
                        "$filter": {
                            "input": "$_joined",
                            "as": "employee",
                            "cond": {
                                "$lte": [
                                    "$$employee.joined_on",
                                    "$date",
                                ]
                            },
                        }
                    }
                }
            }
        },
        {
            "$addFields": {
                "attendance_rate": {
                    "$cond": [
                        {
                            "$and": [
                                "$is_working_day",
                                {"$gt": ["$headcount", 0]},
                            ]
                        },
                        {
                            "$divide": [
                                "$present_count",
                                "$headcount",
                            ]
                        },
                        None,
                    ]
                }
            }
        },
        {
            "$setWindowFields": {
                "sortBy": {"_id": 1},
                "output": {
                    "moving_avg_7d": {
                        "$avg": "$attendance_rate",
                        "window": {"documents": [-6, 0]},
                    }
                },
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": 1,
                "is_working_day": 1,
                "headcount": 1,
                "present_count": 1,
                "late_count": 1,
                "attendance_rate": {
                    "$cond": [
                        {"$eq": ["$attendance_rate", None]},
                        None,
                        {"$round": ["$attendance_rate", 4]},
                    ]
                },
                "moving_avg_7d": {
                    "$cond": [
                        {"$eq": ["$moving_avg_7d", None]},
                        None,
                        {"$round": ["$moving_avg_7d", 4]},
                    ]
                },
            }
        },
        {"$sort": {"date": 1}},
    ]

    rows = list(
        db.attendance_logs.aggregate(
            pipeline,
            allowDiskUse=True,
        )
    )

    for row in rows:
        row["present_count"] = float(
            row.get("present_count", 0)
        )

        if row.get("attendance_rate") is not None:
            row["attendance_rate"] = float(
                round_half_up(row["attendance_rate"], 4)
            )

        if row.get("moving_avg_7d") is not None:
            row["moving_avg_7d"] = float(
                round_half_up(row["moving_avg_7d"], 4)
            )

    return {"department": department, "items": rows}


# --------------------------------------------------
# Admin explain endpoint
# --------------------------------------------------

@app.get("/admin/explain/{endpoint}")
def explain_route(
    endpoint: str,
    emp_code: str | None = None,
    month: str | None = None,
    department: str | None = None,
    limit: int = Query(10, ge=1, le=50),
    date_from: str | None = None,
    date_to: str | None = None,
    status_filter: str | None = Query(None, alias="status"),
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    db = get_db()

    if endpoint == "attendance_list":
        query = {}

        if emp_code:
            query["emp_code"] = emp_code

        if date_from:
            parse_date(date_from)

        if date_to:
            parse_date(date_to)

        if date_from or date_to:
            query["date"] = {}

            if date_from:
                query["date"]["$gte"] = date_from

            if date_to:
                query["date"]["$lte"] = date_to

        if status_filter:
            if status_filter not in ALL_STATUSES:
                raise HTTPException(
                    status_code=422,
                    detail="Invalid status",
                )

            query["status"] = status_filter

        command = {
            "find": "attendance_logs",
            "filter": query,
            "sort": {"date": -1, "emp_code": 1},
            "skip": (page - 1) * page_size,
            "limit": page_size,
        }

        return {
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "explain": db.command(
                "explain",
                command,
                verbosity="executionStats",
            ),
        }

    if endpoint == "employee_monthly":
        if not emp_code or not month:
            raise HTTPException(
                status_code=422,
                detail="emp_code and month are required",
            )

        start, end, _, _ = month_bounds_strings(month)

        pipeline = [
            {
                "$match": {
                    "emp_code": emp_code,
                    "date": {
                        "$gte": start,
                        "$lt": end,
                    },
                }
            },
            {
                "$group": {
                    "_id": "$emp_code",
                    "late_count": {
                        "$sum": {
                            "$cond": [
                                {"$gt": ["$late_minutes", 0]},
                                1,
                                0,
                            ]
                        }
                    },
                    "total_late_minutes": {
                        "$sum": "$late_minutes"
                    },
                }
            },
        ]

        return {
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "explain": db.command(
                "explain",
                {
                    "aggregate": "attendance_logs",
                    "pipeline": pipeline,
                    "cursor": {},
                },
                verbosity="executionStats",
            ),
        }

    if endpoint == "department_summary":
        if not month:
            raise HTTPException(
                status_code=422,
                detail="month is required",
            )

        start, end, _, end_date = month_bounds_strings(month)

        match = {
            "joined_on": {
                "$lte": (end_date - timedelta(days=1)).isoformat()
            }
        }

        if department:
            match["department"] = department

        pipeline = [
            {"$match": match},
            {
                "$lookup": {
                    "from": "attendance_logs",
                    "localField": "emp_code",
                    "foreignField": "emp_code",
                    "as": "logs",
                }
            },
        ]

        return {
            "endpoint": endpoint,
            "collection": "employees",
            "explain": db.command(
                "explain",
                {
                    "aggregate": "employees",
                    "pipeline": pipeline,
                    "cursor": {},
                },
                verbosity="executionStats",
            ),
        }

    if endpoint == "late_leaderboard":
        if not month:
            raise HTTPException(
                status_code=422,
                detail="month is required",
            )

        start, end, _, _ = month_bounds_strings(month)

        query = {
            "date": {"$gte": start, "$lt": end},
            "late_minutes": {"$gt": 0},
        }

        pipeline = [
            {"$match": query},
            {
                "$group": {
                    "_id": "$emp_code",
                    "total_late_minutes": {
                        "$sum": "$late_minutes"
                    },
                }
            },
            {
                "$setWindowFields": {
                    "sortBy": {
                        "total_late_minutes": -1,
                        "_id": 1,
                    },
                    "output": {"rank": {"$rank": {}}},
                }
            },
            {"$match": {"rank": {"$lte": limit}}},
        ]

        return {
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "explain": db.command(
                "explain",
                {
                    "aggregate": "attendance_logs",
                    "pipeline": pipeline,
                    "cursor": {},
                },
                verbosity="executionStats",
            ),
        }

    if endpoint == "department_trend":
        start_text = from_ or date_from
        end_text = to or date_to

        if not department or not start_text or not end_text:
            raise HTTPException(
                status_code=422,
                detail="department, from and to are required",
            )

        parse_date(start_text)
        parse_date(end_text)

        employee_codes = [
            employee["emp_code"]
            for employee in db.employees.find(
                {"department": department},
                {"_id": 0, "emp_code": 1},
            )
        ]

        pipeline = [
            {
                "$match": {
                    "emp_code": {"$in": employee_codes},
                    "date": {
                        "$gte": start_text,
                        "$lte": end_text,
                    },
                }
            },
            {
                "$group": {
                    "_id": "$date",
                    "count": {"$sum": 1},
                }
            },
        ]

        return {
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "explain": db.command(
                "explain",
                {
                    "aggregate": "attendance_logs",
                    "pipeline": pipeline,
                    "cursor": {},
                },
                verbosity="executionStats",
            ),
        }

    raise HTTPException(
        status_code=404,
        detail="Unknown endpoint",
    )


# --------------------------------------------------
# Get one attendance record
# --------------------------------------------------

@app.get("/attendance/{emp_code}/{date_text}")
def get_record(emp_code: str, date_text: str):
    parse_date(date_text)

    record = get_db().attendance_logs.find_one({
        "emp_code": emp_code,
        "date": date_text,
    })

    if not record:
        raise HTTPException(
            status_code=404,
            detail="Attendance record not found",
        )

    return record_out(record)