from collections import Counter

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..database import get_db
from ..models import OLT, OLTPort, User
from ..olt_status_cache import pon_status_cache_key
from ..redis_client import cache

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


def _label_pon(olt: OLT, port: OLTPort) -> str:
    return f"{olt.name} {port.slot}/{port.card or 1}/{port.pon}"


def _count_items(counter: Counter, limit: int = 12):
    return [
        {"label": str(label), "count": count}
        for label, count in counter.most_common(limit)
        if label not in (None, "")
    ]


def _onu_unique_key(olt_id: int, port: OLTPort, onu: dict) -> tuple:
    serial = (onu.get("serial") or "").strip().upper()
    if serial:
        return ("serial", olt_id, serial)
    return ("index", olt_id, (onu.get("onu_index") or "").strip())


def _onu_belongs_to_port(port: OLTPort, onu: dict) -> bool:
    idx = (onu.get("onu_index") or "").strip()
    if not idx:
        return False
    return idx.startswith(f"{port.slot}/{port.card or 1}/{port.pon}:")


def _onu_quality_score(item: dict) -> int:
    score = 0
    if item.get("serial"):
        score += 4
    if item.get("model"):
        score += 3
    if item.get("signal_status") and item.get("signal_status") != "sem leitura":
        score += 3
    if item.get("rx_power") is not None:
        score += 2
    if item.get("oper_state") == "working":
        score += 1
    return score


@router.get("/analytics")
def dashboard_analytics(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    olts = db.query(OLT).order_by(OLT.name).all()
    ports = db.query(OLTPort).order_by(OLTPort.olt_id, OLTPort.slot, OLTPort.card, OLTPort.pon).all()
    olt_by_id = {olt.id: olt for olt in olts}

    pon_capacity = []
    full_pons = []
    warning_pons = []
    signal_counts = Counter()
    model_counts = Counter()
    firmware_counts = Counter()
    state_counts = Counter()
    unique_onus = {}
    cached_ports = 0
    raw_cached_onus = 0
    redis_available = cache.is_available()

    for olt in olts:
        if olt.firmware:
            firmware_counts[olt.firmware] += 1

    for port in ports:
        olt = olt_by_id.get(port.olt_id)
        if not olt:
            continue
        max_onus = port.onu_max or 128
        percent = round((port.onu_count or 0) / max_onus * 100, 1) if max_onus else 0
        item = {
            "olt_id": olt.id,
            "olt": olt.name,
            "slot": port.slot,
            "card": port.card or 1,
            "pon": port.pon,
            "label": _label_pon(olt, port),
            "onu_count": port.onu_count or 0,
            "onu_max": max_onus,
            "percent": percent,
        }
        pon_capacity.append(item)
        if (port.onu_count or 0) >= 115:
            full_pons.append(item)
        elif (port.onu_count or 0) >= 90:
            warning_pons.append(item)

        status = cache.get(pon_status_cache_key(port.olt_id, port.slot, port.card or 1, port.pon)) if redis_available else None
        if not status:
            continue
        cached_ports += 1
        onus = status.get("onus") or []
        raw_cached_onus += len(onus)
        for onu in onus:
            if not _onu_belongs_to_port(port, onu):
                continue
            state = (onu.get("oper_state") or "unknown").lower()
            signal = (onu.get("olt_rx_status") or "sem leitura").lower()
            model = onu.get("model") or ""
            fw = (
                onu.get("firmware")
                or onu.get("software_version")
                or onu.get("current_version")
                or onu.get("version")
                or ""
            )
            item = {
                "olt_id": olt.id,
                "olt": olt.name,
                "slot": port.slot,
                "card": port.card or 1,
                "pon": port.pon,
                "pon_label": _label_pon(olt, port),
                "onu_index": onu.get("onu_index") or "",
                "serial": onu.get("serial") or "",
                "model": model,
                "firmware": fw,
                "admin_state": onu.get("admin_state") or "",
                "oper_state": state,
                "signal_status": signal,
                "rx_power": onu.get("olt_rx_power"),
            }
            key = _onu_unique_key(olt.id, port, onu)
            current = unique_onus.get(key)
            if not current or _onu_quality_score(item) > _onu_quality_score(current):
                unique_onus[key] = item

    onu_items = list(unique_onus.values())
    for item in onu_items:
        state_counts[item["oper_state"]] += 1
        signal_counts[item["signal_status"]] += 1
        if item["model"]:
            model_counts[item["model"]] += 1
        if item["firmware"]:
            firmware_counts[item["firmware"]] += 1

    pon_capacity.sort(key=lambda item: item["onu_count"], reverse=True)

    return {
        "summary": {
            "total_olts": len(olts),
            "total_ports": len(ports),
            "cached_ports": cached_ports,
            "cached_onus": len(onu_items),
            "raw_cached_onus": raw_cached_onus,
            "full_pons": len(full_pons),
            "warning_pons": len(warning_pons),
        },
        "pon_capacity": pon_capacity[:24],
        "full_pons": full_pons,
        "warning_pons": warning_pons,
        "signals": _count_items(signal_counts),
        "models": _count_items(model_counts, 15),
        "firmwares": _count_items(firmware_counts, 15),
        "states": _count_items(state_counts),
        "onus": onu_items,
    }
