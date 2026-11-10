"""Phone matching helpers for public booking lookup / cancel."""

from __future__ import annotations

import re

from phonenumber_field.phonenumber import to_python

# Common East African country codes used for ambiguous local numbers (0…).
_LOOKUP_REGIONS = ("KE", "UG", "SS", "RW", "TZ")


def _digits_only(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def phone_candidates(raw: str) -> list[str]:
    """
    Build plausible E.164 / stored-string candidates for a customer-entered phone.
    """
    raw = (raw or "").strip()
    if not raw:
        return []

    candidates: list[str] = []
    seen: set[str] = set()

    def add(value: str | None) -> None:
        if not value:
            return
        key = str(value)
        if key in seen:
            return
        seen.add(key)
        candidates.append(key)

    add(raw)

    parsed = to_python(raw)
    if parsed is not None and getattr(parsed, "is_valid", lambda: False)():
        add(str(parsed))
        try:
            add(parsed.as_e164)
        except Exception:
            pass

    digits = _digits_only(raw)
    if digits:
        add(f"+{digits}")
        # Local national numbers starting with 0
        if digits.startswith("0") and len(digits) >= 9:
            national = digits.lstrip("0")
            for region in _LOOKUP_REGIONS:
                try:
                    regional = to_python(f"0{national}", region=region)
                except TypeError:
                    # Older phonenumber_field: to_python(value) only
                    regional = None
                if regional is None:
                    continue
                if getattr(regional, "is_valid", lambda: False)():
                    add(str(regional))
                    try:
                        add(regional.as_e164)
                    except Exception:
                        pass

        for cc in ("254", "256", "211", "250", "255"):
            if digits.startswith(cc):
                add(f"+{digits}")

    return candidates


def phones_match(stored, provided: str) -> bool:
    """Return True when a customer-entered phone matches a stored PhoneNumber."""
    if stored is None or not provided:
        return False

    stored_str = str(stored)
    stored_digits = _digits_only(stored_str)
    provided_digits = _digits_only(provided)

    if provided_digits and provided_digits == stored_digits:
        return True

    # Match on national significant number (last 9 digits) — common when users
    # omit / swap country codes (+254 vs 07…).
    if len(provided_digits) >= 9 and len(stored_digits) >= 9:
        if provided_digits[-9:] == stored_digits[-9:]:
            return True

    for candidate in phone_candidates(provided):
        if candidate == stored_str:
            return True
        cand_digits = _digits_only(candidate)
        if cand_digits and cand_digits == stored_digits:
            return True
        try:
            parsed = to_python(candidate)
            if parsed is not None and str(parsed) == stored_str:
                return True
        except Exception:
            continue

    return False
