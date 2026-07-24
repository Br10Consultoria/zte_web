import logging
import json
import threading
import time
from collections import Counter
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from .config import settings
from .database import SessionLocal
from .models import OLT, OLTPort, OLTStatusSnapshot, PONStatusSnapshot, ONUStateEvent
from .olt_client import OLTConnectionError, get_olt_client
from .olt_driver import get_driver
from .redis_client import cache

logger = logging.getLogger("routes.olts")

_refresh_lock = threading.Lock()
_scheduler_started = False


def _now_iso() -> str:
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("America/Sao_Paulo")).strftime("%d/%m/%Y %H:%M:%S")
    except Exception:
        return datetime.now().strftime("%d/%m/%Y %H:%M:%S")


def pon_status_cache_key(olt_id: int, slot: int, card: int, pon: int) -> str:
    return f"olt:{olt_id}:pon:{slot}:{card}:{pon}:status"


def _onu_id(onu: dict) -> int:
    try:
        return int(str(onu.get("onu_index") or "").split(":")[-1])
    except (TypeError, ValueError):
        return 0


def _signal_state(onu: dict) -> str:
    value = onu.get("olt_rx_power")
    try:
        if value is not None and float(value) <= -28:
            return "rx_critical"
    except (TypeError, ValueError):
        pass
    return (onu.get("olt_rx_status") or "unknown").lower()


def _record_port_history(db: Session, olt: OLT, port: OLTPort, result: Dict, previous: Optional[Dict]) -> None:
    now = datetime.utcnow()
    counts = Counter((onu.get("oper_state") or "unknown").lower() for onu in result.get("onus", []))
    db.add(PONStatusSnapshot(
        olt_id=olt.id,
        slot=port.slot,
        card=port.card or 1,
        pon=port.pon,
        total_onus=result.get("total", 0),
        online_onus=result.get("online", 0),
        offline_onus=result.get("offline", 0),
        status_counts=json.dumps(dict(counts)),
        captured_at=now,
    ))

    previous_map = {
        str(item.get("onu_index") or ""): item
        for item in (previous or {}).get("onus", [])
    }
    if not previous_map:
        latest_events = db.query(ONUStateEvent).filter(
            ONUStateEvent.olt_id == olt.id,
            ONUStateEvent.slot == port.slot,
            ONUStateEvent.card == (port.card or 1),
            ONUStateEvent.pon == port.pon,
        ).order_by(ONUStateEvent.observed_at.desc()).all()
        for event in latest_events:
            idx = f"{port.slot}/{port.card or 1}/{port.pon}:{event.onu_id}"
            if idx in previous_map:
                continue
            previous_map[idx] = {
                "onu_index": idx,
                "serial": event.serial,
                "oper_state": event.current_state,
                "olt_rx_status": event.current_signal,
            }
    for onu in result.get("onus", []):
        idx = str(onu.get("onu_index") or "")
        old = previous_map.get(idx)
        current_state = (onu.get("oper_state") or "unknown").lower()
        current_signal = _signal_state(onu)
        previous_state = (old.get("oper_state") or "unknown").lower() if old else None
        previous_signal = _signal_state(old) if old else None
        if old and previous_state == current_state and previous_signal == current_signal:
            continue
        db.add(ONUStateEvent(
            olt_id=olt.id,
            slot=port.slot,
            card=port.card or 1,
            pon=port.pon,
            onu_id=_onu_id(onu),
            serial=onu.get("serial") or None,
            previous_state=previous_state,
            current_state=current_state,
            previous_signal=previous_signal,
            current_signal=current_signal,
            reason=onu.get("last_down_cause") or None,
            observed_at=now,
        ))


def _record_olt_snapshot(
    db: Session,
    olt_id: int,
    port_results: List[Dict],
    olt_status: str = "online",
) -> None:
    counts = Counter()
    for result in port_results:
        counts.update((onu.get("oper_state") or "unknown").lower() for onu in result.get("onus", []))
    online = counts.get("working", 0)
    total = sum(counts.values())
    db.add(OLTStatusSnapshot(
        olt_id=olt_id,
        olt_status=olt_status,
        total_onus=total,
        online_onus=online,
        offline_onus=max(total - online, 0),
        status_counts=json.dumps(dict(counts)),
        captured_at=datetime.utcnow(),
    ))


def _prune_history(db: Session) -> None:
    cutoff = datetime.utcnow() - timedelta(days=30)
    db.query(OLTStatusSnapshot).filter(OLTStatusSnapshot.captured_at < cutoff).delete(synchronize_session=False)
    db.query(PONStatusSnapshot).filter(PONStatusSnapshot.captured_at < cutoff).delete(synchronize_session=False)
    db.query(ONUStateEvent).filter(ONUStateEvent.observed_at < cutoff).delete(synchronize_session=False)


def collect_pon_status(client, driver, olt: OLT, port: OLTPort, include_details: bool = True) -> Dict:
    iface = driver.olt_iface(port.slot, port.card, port.pon)

    output = client.execute_command(driver.cmd_onu_state(iface), timeout=30)
    onus = driver.parse_onu_state(output)

    base_map = {}
    base_serial_map = {}
    try:
        base_out = client.execute_command(driver.cmd_onu_baseinfo(iface), timeout=30)
        base_items = driver.parse_onu_baseinfo(base_out)
        base_map = {b["onu_index"]: b for b in base_items if b.get("onu_index")}
        base_serial_map = {
            str(b.get("serial", "")).upper(): b
            for b in base_items
            if b.get("serial")
        }
    except Exception as exc:
        logger.warning(f"[CACHE] Falha baseinfo {iface}: {exc}")

    rx_map = {}
    try:
        rx_out = client.execute_command(driver.cmd_olt_rx(iface), timeout=30)
        rx_map = driver.parse_olt_rx(rx_out)
    except Exception as exc:
        logger.warning(f"[CACHE] Falha RX OLT {iface}: {exc}")

    detail_map = {}
    power_map = {}
    if include_details:
        for onu_item in onus:
            idx = onu_item["onu_index"]
            onu_iface = driver.onu_iface(idx)
            try:
                det_out = client.execute_command(driver.cmd_onu_detail(onu_iface), timeout=10)
                detail_map[idx] = driver.parse_onu_detail(det_out)
            except Exception as exc:
                logger.warning(f"[CACHE] Falha detail-info {onu_iface}: {exc}")
                detail_map[idx] = {}
            try:
                power_out = client.execute_command(driver.cmd_onu_power(onu_iface), timeout=10)
                power_map[idx] = driver.parse_onu_power(power_out)
            except Exception as exc:
                logger.warning(f"[CACHE] Falha power {onu_iface}: {exc}")
                power_map[idx] = {}

    for onu in onus:
        idx = onu["onu_index"]
        serial_key = str(onu.get("serial", "")).upper()
        base = base_map.get(idx) or base_serial_map.get(serial_key, {})
        detail = detail_map.get(idx, {})
        onu["serial"] = onu.get("serial") or base.get("serial", "")
        onu["model"] = base.get("model", onu.get("model", ""))
        onu["description"] = detail.get("description", onu.get("description", ""))
        onu["online_duration"] = detail.get("online_duration", onu.get("online_duration", ""))
        power = power_map.get(idx, {})
        if power:
            if power.get("rx_power") is not None:
                onu["rx_power"] = power.get("rx_power")
                onu["onu_rx_power"] = power.get("rx_power")
            if power.get("rx_status") is not None:
                onu["rx_status"] = power.get("rx_status")
            if power.get("tx_power") is not None:
                onu["tx_power"] = power.get("tx_power")

        rx_val = rx_map.get(idx)
        if rx_val is not None:
            onu["olt_rx_power"] = rx_val
            if rx_val >= -27:
                onu["olt_rx_status"] = "normal"
            elif rx_val > -29:
                onu["olt_rx_status"] = "warning"
            else:
                onu["olt_rx_status"] = "critical"
        else:
            onu["olt_rx_power"] = onu.get("olt_rx_power")
            onu["olt_rx_status"] = onu.get("olt_rx_status")

    online = sum(1 for onu in onus if onu["oper_state"] == "working")
    offline = sum(1 for onu in onus if onu["oper_state"] not in ("working", "initial", "ranging"))

    return {
        "olt_id": olt.id,
        "slot": port.slot,
        "card": port.card,
        "pon": port.pon,
        "olt_interface": iface,
        "olt_model": olt.olt_model,
        "onus": onus,
        "total": len(onus),
        "online": online,
        "offline": offline,
        "cached": False,
        "details_included": include_details,
        "cache_expires_in": None,
        "last_updated": _now_iso(),
    }


def refresh_olt_ports_status(
    olt_id: int,
    db: Optional[Session] = None,
    include_details: bool = False,
) -> Dict:
    owns_db = db is None
    if db is None:
        db = SessionLocal()

    client = None
    summary = {"olt_id": olt_id, "updated": 0, "failed": 0, "errors": []}
    port_results = []

    try:
        olt = db.query(OLT).filter(OLT.id == olt_id).first()
        if not olt:
            summary["errors"].append("OLT nao encontrada")
            return summary

        ports: List[OLTPort] = db.query(OLTPort).filter(OLTPort.olt_id == olt_id).order_by(
            OLTPort.slot, OLTPort.card, OLTPort.pon
        ).all()
        if not ports:
            summary["errors"].append("Nenhuma porta PON descoberta")
            return summary

        driver = get_driver(olt.olt_model)
        client = get_olt_client(
            olt.ip, olt.port, olt.username, olt.password, olt.protocol, olt.olt_model
        )
        client.connect()

        for port in ports:
            iface = driver.olt_iface(port.slot, port.card, port.pon)
            try:
                cache_key = pon_status_cache_key(olt_id, port.slot, port.card, port.pon)
                previous = cache.get(cache_key)
                result = collect_pon_status(client, driver, olt, port, include_details=include_details)
                _record_port_history(db, olt, port, result, previous)
                cache.set(cache_key, result)
                port_results.append(result)
                port.onu_count = result["total"]
                port.status = "online" if result["online"] > 0 else "active"
                summary["updated"] += 1
                logger.info(f"[CACHE] {iface}: {result['total']} ONUs salvas no Redis")
            except Exception as exc:
                port.status = "unknown"
                summary["failed"] += 1
                summary["errors"].append(f"{iface}: {exc}")
                logger.error(f"[CACHE] Erro ao atualizar {iface}: {exc}", exc_info=True)

        if port_results:
            _record_olt_snapshot(db, olt_id, port_results)
        _prune_history(db)
        olt.status = "online" if summary["updated"] else "offline"
        olt.last_check = datetime.now()
        db.commit()
        return summary

    except OLTConnectionError as exc:
        db.rollback()
        summary["failed"] += 1
        summary["errors"].append(str(exc))
        logger.error(f"[CACHE] Erro de conexao ao atualizar OLT {olt_id}: {exc}", exc_info=True)
        try:
            olt = db.query(OLT).filter(OLT.id == olt_id).first()
            if olt:
                olt.status = "offline"
                olt.last_check = datetime.now()
                _record_olt_snapshot(db, olt_id, [], olt_status="offline")
                _prune_history(db)
                db.commit()
        except Exception:
            db.rollback()
        return summary
    except Exception as exc:
        db.rollback()
        summary["failed"] += 1
        summary["errors"].append(str(exc))
        logger.error(f"[CACHE] Erro inesperado ao atualizar OLT {olt_id}: {exc}", exc_info=True)
        return summary
    finally:
        if client:
            try:
                client.disconnect()
            except Exception:
                pass
        if owns_db:
            db.close()


def refresh_all_olts_once() -> Dict:
    if not _refresh_lock.acquire(blocking=False):
        logger.info("[SCHED] Atualizacao anterior ainda em andamento; pulando ciclo")
        return {"skipped": True}

    db = SessionLocal()
    totals = {"olts": 0, "updated": 0, "failed": 0}
    try:
        olt_ids = [row[0] for row in db.query(OLT.id).all()]
        for olt_id in olt_ids:
            totals["olts"] += 1
            result = refresh_olt_ports_status(olt_id, db=db, include_details=False)
            totals["updated"] += result.get("updated", 0)
            totals["failed"] += result.get("failed", 0)
        logger.info(f"[SCHED] Ciclo concluido: {totals}")
        return totals
    finally:
        db.close()
        _refresh_lock.release()


def start_hourly_status_refresh() -> None:
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True

    interval = max(int(getattr(settings, "AUTO_REFRESH_INTERVAL", 3600)), 60)

    def _runner():
        logger.info(f"[SCHED] Atualizacao automatica iniciada a cada {interval}s")
        time.sleep(10)
        while True:
            refresh_all_olts_once()
            time.sleep(interval)

    thread = threading.Thread(target=_runner, name="olt-hourly-status-refresh", daemon=True)
    thread.start()
