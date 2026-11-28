"""Saved searches and preferences (signed in): /api/searches, /api/prefs."""

from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Response

from logr.searches import PREF_KEYS, Filter
from logr.sources.luma import resolve_place

from .deps import UserViewDep, View, bad_request, not_found
from .schemas import PrefIn, PrefOut, PrefSet, SearchIn, SearchOut

router = APIRouter(prefix="/api")


def _norm_name(name: str) -> str:
    return name.strip().lower()


def _default_name(view: View) -> str | None:
    d = view.prefs.get("default_search")
    return _norm_name(d) if d else None


def _search_out(name: str, f: Filter, default: str | None) -> SearchOut:
    return SearchOut(name=name, filter=f.to_dict(), description=f.describe(), is_default=default == name)


@router.get("/searches", response_model=list[SearchOut])
def list_searches(view: UserViewDep) -> list[SearchOut]:
    default = _default_name(view)
    return [_search_out(n, f, default) for n, f in view.searches.all()]


@router.put("/searches/{name}", response_model=SearchOut)
def save_search(name: str, body: SearchIn, view: UserViewDep) -> SearchOut:
    try:
        f = Filter.from_dict(body.filter)
    except (ValueError, TypeError) as exc:
        raise bad_request(f"bad filter: {exc}") from None
    if f.is_empty():
        raise bad_request("nothing to save; give at least one filter criterion")
    try:
        view.searches.save(name, f)
    except ValueError as exc:
        raise bad_request(str(exc)) from None
    key = _norm_name(name)
    return _search_out(key, view.searches.get(key) or f, _default_name(view))


@router.delete("/searches/{name}", status_code=204)
def delete_search(name: str, view: UserViewDep) -> Response:
    key = _norm_name(name)
    if not view.searches.delete(key):
        raise not_found(f"no saved search {key!r}")
    if _default_name(view) == key:
        view.prefs.unset("default_search")
    return Response(status_code=204)


@router.get("/prefs", response_model=dict[str, PrefOut])
def get_prefs(view: UserViewDep) -> dict[str, PrefOut]:
    values = view.prefs.all()
    return {k: PrefOut(value=values.get(k), description=desc) for k, desc in PREF_KEYS.items()}


def validate_pref(key: str, value: str, view: View) -> str:
    """Same rules as ``logr prefs set`` (cli._validate_pref); returns the value to store."""
    value = value.strip()
    if key == "tz":
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            raise bad_request(f"unknown timezone {value!r} (use an IANA name like Asia/Kolkata)") from None
    elif key == "default_search":
        value = _norm_name(value)
        if not value or view.searches.get(value) is None:
            raise bad_request(f"no saved search {value!r}")
    elif key == "luma_near":
        places = [p.strip() for p in value.split(";") if p.strip()]
        if not places:
            raise bad_request("luma_near needs at least one place")
        for p in places:
            try:
                resolve_place(p)
            except ValueError as exc:
                raise bad_request(str(exc)) from None
        value = ";".join(places)
    return value


@router.put("/prefs/{key}", response_model=PrefSet)
def set_pref(key: str, body: PrefIn, view: UserViewDep) -> PrefSet:
    if key not in PREF_KEYS:
        raise not_found(f"unknown preference {key!r}")
    value = validate_pref(key, body.value, view)
    view.prefs.set(key, value)
    return PrefSet(key=key, value=value, description=PREF_KEYS[key])


@router.delete("/prefs/{key}", status_code=204)
def unset_pref(key: str, view: UserViewDep) -> Response:
    if key not in PREF_KEYS:
        raise not_found(f"unknown preference {key!r}")
    view.prefs.unset(key)
    return Response(status_code=204)
