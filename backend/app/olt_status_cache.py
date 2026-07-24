import logging
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from .config import settings
from .database import SessionLocal
from .models import OLT, OLTPort
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
                result = collect_pon_status(client, driver, olt, port, include_details=include_details)
                cache.set(pon_status_cache_key(olt_id, port.slot, port.card, port.pon), result)
                port.onu_count = result["total"]
                port.status = "online" if result["online"] > 0 else "active"
                summary["updated"] += 1
                logger.info(f"[CACHE] {iface}: {result['total']} ONUs salvas no Redis")
            except Exception as exc:
                port.status = "unknown"
                summary["failed"] += 1
                summary["errors"].append(f"{iface}: {exc}")
                logger.error(f"[CACHE] Erro ao atualizar {iface}: {exc}", exc_info=True)

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
        while True:
            time.sleep(interval)
            refresh_all_olts_once()

    thread = threading.Thread(target=_runner, name="olt-hourly-status-refresh", daemon=True)
    thread.start()
