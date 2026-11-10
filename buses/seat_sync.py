"""Generate and sync Seat rows from a bus VIP / regular configuration."""
import re

from django.core.exceptions import ValidationError
from django.db import transaction

from seats.models import Seat

LAYOUT_PATTERN = re.compile(r"^[1-4]\+[1-4]$")


def parse_layout(layout: str) -> tuple[int, int]:
    value = (layout or "2+2").strip()
    if not LAYOUT_PATTERN.fullmatch(value):
        raise ValidationError(
            "Seat layout must be N+M with 1–4 seats on each side of the aisle, for example 1+2 or 2+3."
        )
    left, right = value.split("+")
    return int(left), int(right)


def column_letters(layout: str) -> list[str]:
    left, right = parse_layout(layout)
    cols = [chr(65 + i) for i in range(left)]
    cols.extend(chr(65 + left + i) for i in range(right))
    return cols


def seat_position(col_idx: int, total_cols: int, left_count: int | None = None) -> str:
    if col_idx == 0 or col_idx == total_cols - 1:
        return "WINDOW"
    if left_count is not None and col_idx != left_count - 1 and col_idx != left_count:
        return "MIDDLE"
    return "AISLE"


def build_seat_plan(layout: str, vip_count: int, regular_count: int) -> list[dict]:
    """Fill seats left-to-right, front-to-back. The first vip_count seats are VIP."""
    columns = column_letters(layout)
    left, _right = parse_layout(layout)
    vip_count = max(int(vip_count or 0), 0)
    regular_count = max(int(regular_count or 0), 0)
    total = vip_count + regular_count
    plan: list[dict] = []
    for index in range(total):
        row = index // len(columns) + 1
        col_idx = index % len(columns)
        plan.append({
            "seat_number": str(index + 1),
            "row": row,
            "column": columns[col_idx],
            "position": seat_position(col_idx, len(columns), left),
            "seat_type": "VIP" if index < vip_count else "REGULAR",
        })
    return plan


def protected_seat_ids(bus) -> set[int]:
    return set(
        Seat.objects.filter(bus=bus, tickets__status="VALID").values_list("id", flat=True)
    )


def _refresh_bus_counts(bus, layout: str) -> None:
    active = bus.seats.filter(is_active=True)
    bus.vip_seats = active.filter(seat_type="VIP").count()
    bus.regular_seats = active.filter(seat_type="REGULAR").count()
    bus.capacity = bus.vip_seats + bus.regular_seats
    bus.seat_layout = layout
    bus.total_rows = max((seat.row for seat in active), default=1)
    bus.save(update_fields=["vip_seats", "regular_seats", "capacity", "seat_layout", "total_rows", "updated_at"])


@transaction.atomic
def sync_bus_seats(bus) -> dict:
    """
    Align Seat rows with bus.vip_seats / bus.regular_seats.

    Unused seats can be created, reclassified, or deactivated.
    Seats with VALID tickets are never deleted or reclassified.
    """
    plan = build_seat_plan(bus.seat_layout, bus.vip_seats, bus.regular_seats)
    for item in plan:
        item["is_active"] = True
    result = apply_explicit_seat_plan(bus, bus.seat_layout, plan)
    return result


def _seat_id(raw_id) -> int | None:
    if raw_id in (None, ""):
        return None
    try:
        return int(raw_id)
    except (TypeError, ValueError):
        return None


@transaction.atomic
def apply_explicit_seat_plan(bus, layout: str, seats: list[dict]) -> dict:
    """
    Persist a manager-drawn floorplan.
    Prefer matching by seat id so layouts like 2+1 → 1+2 can move seats
    across the aisle. Fall back to (row, column). Sold seats cannot move,
    change type, or be removed.
    """
    columns = column_letters(layout)
    left, _right = parse_layout(layout)
    col_index = {col: idx for idx, col in enumerate(columns)}

    plan: list[dict] = []
    seen_positions: set[tuple[int, str]] = set()
    seen_ids: set[int] = set()
    for raw in seats:
        column = str(raw.get("column", "")).upper().strip()
        row = int(raw.get("row") or 0)
        if row < 1:
            raise ValidationError("Each seat needs a row number of 1 or more.")
        if column not in col_index:
            raise ValidationError(
                f"Seat {row}{column} does not fit layout {layout}. "
                f"Allowed columns: {', '.join(columns)}."
            )
        seat_type = raw.get("seat_type") or "REGULAR"
        if seat_type not in ("VIP", "REGULAR"):
            raise ValidationError(f"Invalid seat type: {seat_type}.")
        key = (row, column)
        if key in seen_positions:
            raise ValidationError(f"Duplicate seat at {row}{column}.")
        seen_positions.add(key)
        seat_id = _seat_id(raw.get("id"))
        if seat_id is not None:
            if seat_id in seen_ids:
                raise ValidationError(f"Duplicate seat id {seat_id}.")
            seen_ids.add(seat_id)
        number = str(raw.get("seat_number") or f"{row}{column}")[:5]
        plan.append({
            "id": seat_id,
            "seat_number": number,
            "row": row,
            "column": column,
            "position": seat_position(col_index[column], len(columns), left),
            "seat_type": seat_type,
            "is_active": bool(raw.get("is_active", True)),
        })

    if not any(item["is_active"] for item in plan):
        raise ValidationError("A bus needs at least one active passenger seat.")

    existing = list(bus.seats.all())
    by_id = {seat.id: seat for seat in existing}
    used_numbers = {seat.seat_number for seat in existing}
    protected = protected_seat_ids(bus)
    claimed: set[int] = set()
    assignments: list[tuple[dict, Seat | None]] = []

    for item in plan:
        seat = None
        if item["id"] is not None:
            candidate = by_id.get(item["id"])
            if candidate is not None and candidate.id not in claimed:
                seat = candidate
                claimed.add(candidate.id)
        assignments.append((item, seat))

    by_pos = {(seat.row, seat.column): seat for seat in existing if seat.id not in claimed}
    resolved: list[tuple[dict, Seat | None]] = []
    for item, seat in assignments:
        if seat is None:
            pos_seat = by_pos.get((item["row"], item["column"]))
            if pos_seat is not None:
                seat = pos_seat
                claimed.add(pos_seat.id)
                del by_pos[(item["row"], item["column"])]
        resolved.append((item, seat))

    blocked: list[str] = []
    for item, seat in resolved:
        if seat is None or seat.id not in protected:
            continue
        if seat.row != item["row"] or seat.column != item["column"]:
            blocked.append(f"seat {seat.seat_number} ({seat.row}{seat.column}) has a valid ticket")
        elif seat.seat_type != item["seat_type"] or (seat.is_active and not item["is_active"]):
            blocked.append(f"seat {seat.seat_number} ({seat.row}{seat.column}) has a valid ticket")

    unused = [seat for seat in existing if seat.id not in claimed]
    for seat in unused:
        if seat.id in protected:
            blocked.append(f"seat {seat.seat_number} ({seat.row}{seat.column}) has a valid ticket")

    if blocked:
        raise ValidationError(
            "Cannot apply this layout because these seats already have valid tickets: "
            + ", ".join(blocked)
            + ". Wait until those trips complete, or keep those seats as they are."
        )

    def unique_number(desired: str) -> str:
        candidate = desired[:5] or "1"
        if candidate not in used_numbers:
            return candidate
        n = 1
        while str(n) in used_numbers:
            n += 1
        return str(n)

    deactivated = 0
    for index, seat in enumerate(unused):
        was_active = seat.is_active
        seat.row = 9100 + index
        seat.column = "Z"
        seat.is_active = False
        seat.save(update_fields=["row", "column", "is_active", "updated_at"])
        if was_active:
            deactivated += 1

    movers = [
        seat
        for item, seat in resolved
        if seat is not None and (seat.row != item["row"] or seat.column != item["column"])
    ]
    for index, seat in enumerate(movers):
        seat.row = 9200 + index
        seat.column = "Y"
        seat.save(update_fields=["row", "column", "updated_at"])

    created = 0
    updated = 0
    for item, seat in resolved:
        if seat is None:
            number = unique_number(item["seat_number"])
            used_numbers.add(number)
            Seat.objects.create(
                bus=bus,
                seat_number=number,
                row=item["row"],
                column=item["column"],
                position=item["position"],
                seat_type=item["seat_type"],
                is_active=item["is_active"],
            )
            created += 1
            continue

        changed = False
        for field, value in (
            ("row", item["row"]),
            ("column", item["column"]),
            ("position", item["position"]),
            ("seat_type", item["seat_type"]),
            ("is_active", item["is_active"]),
        ):
            if getattr(seat, field) != value:
                setattr(seat, field, value)
                changed = True
        if changed:
            seat.save()
            updated += 1

    _refresh_bus_counts(bus, layout)
    return {"created": created, "updated": updated, "deactivated": deactivated}
