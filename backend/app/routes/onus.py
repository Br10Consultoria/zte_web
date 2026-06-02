"""
Rotas de consulta de ONUs.
Suporta múltiplos modelos via olt_driver.py:
  zte_c320 — ZTE C320/C600/C610/C620/C650 (gpon-olt_1/CARD/PON)
  zte_c300 — ZTE C300/C300M/C300T Titan   (gpon_olt-SLOT/CARD/PON)
"""
import re
import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from datetime import datetime
from typing import Optional
try:
    from zoneinfo import ZoneInfo
    _SP = ZoneInfo('America/Sao_Paulo')
except ImportError:
    _SP = None

def _now_iso():
    if _SP:
        return datetime.now(_SP).strftime('%d/%m/%Y %H:%M:%S')
    return datetime.now().strftime('%d/%m/%Y %H:%M:%S')

from ..database import get_db
from ..models import User, OLT, OLTPort
from ..auth import get_current_user
from ..olt_client import (
    get_olt_client, OLTConnectionError,
    parse_onu_power, parse_onu_detail, get_onu_full_details,
    reboot_onu, get_onu_traffic
)
from ..olt_driver import get_driver
from ..redis_client import cache

router = APIRouter(prefix="/onus", tags=["ONUs"])
logger = logging.getLogger("routes.onus")


def _get_olt_or_404(olt_id: int, db: Session) -> OLT:
    olt = db.query(OLT).filter(OLT.id == olt_id).first()
    if not olt:
        raise HTTPException(status_code=404, detail="OLT não encontrada")
    return olt


def _get_port(olt_id: int, slot: int, card: int, pon: int, db: Session) -> Optional[OLTPort]:
    return db.query(OLTPort).filter(
        OLTPort.olt_id == olt_id,
        OLTPort.slot == slot,
        OLTPort.card == card,
        OLTPort.pon == pon
    ).first()


@router.get("/{olt_id}/pon/{slot}/{card}/{pon}/status")
def get_pon_status(
    olt_id: int,
    slot: int,
    card: int,
    pon: int,
    force_refresh: bool = Query(False, description="Forçar atualização ignorando cache"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Retorna o status de todas as ONUs de uma porta PON.
    Usa o driver correto para o modelo da OLT cadastrada.
    Cache Redis por 24 horas. Use force_refresh=true para atualizar.
    """
    olt = _get_olt_or_404(olt_id, db)
    port_obj = _get_port(olt_id, slot, card, pon, db)
    driver = get_driver(olt.olt_model)

    cache_key = f"olt:{olt_id}:pon:{slot}:{card}:{pon}:status"

    if not force_refresh:
        cached_data = cache.get(cache_key)
        if cached_data:
            cached_data["cached"] = True
            cache_info = cache.get_cache_info(cache_key)
            cached_data["cache_expires_in"] = cache_info.get("expires_in")
            return cached_data

    iface = driver.olt_iface(slot, card, pon)
    logger.info(f"[PON_STATUS] Consultando {iface} na OLT {olt.ip} (modelo: {olt.olt_model})")

    try:
        client = get_olt_client(olt.ip, olt.port, olt.username, olt.password, olt.protocol)
        client.connect()

        # Estado das ONUs
        cmd_state = driver.cmd_onu_state(iface)
        logger.info(f"[PON_STATUS] Executando: {cmd_state}")
        output = client.execute_command(cmd_state, timeout=30)
        logger.info(f"[PON_STATUS] Output bruto ({len(output)} chars): {output[:500]}")
        onus = driver.parse_onu_state(output)
        logger.info(f"[PON_STATUS] {len(onus)} ONUs parseadas")

        # Baseinfo (serial + modelo)
        cmd_base = driver.cmd_onu_baseinfo(iface)
        logger.info(f"[PON_STATUS] Executando: {cmd_base}")
        base_out = client.execute_command(cmd_base, timeout=30)
        base_list = driver.parse_onu_baseinfo(base_out)
        base_map = {b["onu_index"]: b for b in base_list}

        # RX OLT em batch
        rx_map = {}
        try:
            cmd_rx = driver.cmd_olt_rx(iface)
            logger.info(f"[PON_STATUS] Executando: {cmd_rx}")
            rx_out = client.execute_command(cmd_rx, timeout=30)
            rx_map = driver.parse_olt_rx(rx_out)
            logger.info(f"[PON_STATUS] RX coletado para {len(rx_map)} ONUs")
        except Exception as rx_err:
            logger.warning(f"[PON_STATUS] Falha ao coletar RX OLT: {rx_err}")

        # Detail-info individual por ONU (description + online_duration)
        detail_map = {}
        MAX_DETAIL = 60
        onus_for_detail = onus[:MAX_DETAIL]
        logger.info(f"[PON_STATUS] Coletando detail-info para {len(onus_for_detail)} ONUs")
        for onu_item in onus_for_detail:
            idx = onu_item["onu_index"]  # ex: 1/1/12:1
            onu_iface = driver.onu_iface(idx)  # ex: gpon-onu_1/1/12:1 ou gpon_onu-1/1/12:1
            try:
                cmd_detail = driver.cmd_onu_detail(onu_iface)
                det_out = client.execute_command(cmd_detail, timeout=10)
                desc = ""
                m_desc = re.search(r'Description\s*:\s*(\S[^\n]*)', det_out)
                if m_desc:
                    desc = m_desc.group(1).strip()
                uptime = ""
                m_up = re.search(r'Online Duration\s*:\s*(\S[^\n]*)', det_out)
                if m_up:
                    uptime = m_up.group(1).strip()
                detail_map[idx] = {"description": desc, "online_duration": uptime}
            except Exception as det_err:
                logger.warning(f"[PON_STATUS] Falha detail-info {onu_iface}: {det_err}")
                detail_map[idx] = {"description": "", "online_duration": ""}
        logger.info(f"[PON_STATUS] Detail-info coletado para {len(detail_map)} ONUs")

        client.disconnect()

        # Mescla baseinfo + RX + detail com estado
        for onu in onus:
            idx = onu["onu_index"]
            base   = base_map.get(idx, {})
            detail = detail_map.get(idx, {})
            onu["serial"]          = base.get("serial", "")
            onu["model"]           = base.get("model", "")
            onu["description"]     = detail.get("description", "")
            onu["online_duration"] = detail.get("online_duration", "")
            rx_val = rx_map.get(idx)
            if rx_val is not None:
                onu["olt_rx_power"] = rx_val
                if rx_val >= -25:
                    onu["olt_rx_status"] = "normal"
                elif rx_val >= -28:
                    onu["olt_rx_status"] = "warning"
                else:
                    onu["olt_rx_status"] = "critical"
            else:
                onu["olt_rx_power"] = None
                onu["olt_rx_status"] = None

        # Atualiza contagem na porta
        if port_obj:
            port_obj.onu_count = len(onus)
            port_obj.status = "online" if any(o["oper_state"] == "working" for o in onus) else "active"
            db.commit()

        online  = sum(1 for o in onus if o["oper_state"] == "working")
        offline = sum(1 for o in onus if o["oper_state"] not in ("working", "initial", "ranging"))

        result = {
            "olt_id":        olt_id,
            "slot":          slot,
            "card":          card,
            "pon":           pon,
            "olt_interface": iface,
            "olt_model":     olt.olt_model,
            "onus":          onus,
            "total":         len(onus),
            "online":        online,
            "offline":       offline,
            "cached":        False,
            "cache_expires_in": None,
            "last_updated":  _now_iso()
        }

        cache.set(cache_key, result)
        return result

    except OLTConnectionError as e:
        logger.error(f"[PON_STATUS] Erro de conexão: {e}")
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error(f"[PON_STATUS] Erro inesperado: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Erro interno: {str(e)}")


@router.get("/{olt_id}/pon/{slot}/{card}/{pon}/onu/{onu_id}/full")
def get_onu_full_info(
    olt_id: int,
    slot: int,
    card: int,
    pon: int,
    onu_id: int,
    force_refresh: bool = Query(False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Retorna informações completas de uma ONU:
    - Detalhes: nome, tipo, serial, descrição, distância, perfis, histórico de quedas
    - Potência: OLT Rx/Tx, ONU Rx/Tx, atenuação upstream/downstream
    - Estado operacional atual
    """
    olt = _get_olt_or_404(olt_id, db)
    driver = get_driver(olt.olt_model)
    cache_key = f"olt:{olt_id}:onu:{slot}:{card}:{pon}:{onu_id}:full"

    if not force_refresh:
        cached_data = cache.get(cache_key)
        if cached_data:
            cached_data["cached"] = True
            cache_info = cache.get_cache_info(cache_key)
            cached_data["cache_expires_in"] = cache_info.get("expires_in")
            return cached_data

    try:
        result = get_onu_full_details(
            olt.ip, olt.port, olt.username, olt.password, olt.protocol,
            slot, card, pon, onu_id,
            driver=driver
        )
        result["olt_id"]  = olt_id
        result["cached"]  = False
        result["last_updated"] = _now_iso()

        cache.set(cache_key, result)
        return result

    except OLTConnectionError as e:
        logger.error(f"[ONU_FULL] Erro de conexão: {e}")
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error(f"[ONU_FULL] Erro inesperado: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Erro interno: {str(e)}")


@router.get("/{olt_id}/unconfigured")
def get_unconfigured_onus(
    olt_id: int,
    force_refresh: bool = Query(False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Retorna ONUs não provisionadas (aguardando autorização)."""
    olt = _get_olt_or_404(olt_id, db)
    driver = get_driver(olt.olt_model)
    cache_key = f"olt:{olt_id}:uncfg"

    if not force_refresh:
        cached_data = cache.get(cache_key)
        if cached_data:
            cached_data["cached"] = True
            return cached_data

    try:
        client = get_olt_client(olt.ip, olt.port, olt.username, olt.password, olt.protocol)
        client.connect()
        # Comando para ONUs não configuradas (igual para todos os modelos ZTE)
        out = client.execute_command("show gpon onu uncfg", timeout=30)
        client.disconnect()

        from ..olt_client import parse_uncfg_onus
        onus = parse_uncfg_onus(out)
        result = {
            "olt_id": olt_id,
            "onus":   onus,
            "total":  len(onus),
            "cached": False,
            "last_updated": _now_iso()
        }
        cache.set(cache_key, result)
        return result
    except OLTConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/{olt_id}/search")
def search_onu(
    olt_id: int,
    serial: Optional[str] = Query(None, description="Número de série da ONU"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Busca uma ONU pelo número de série em todas as portas PON."""
    olt = _get_olt_or_404(olt_id, db)
    driver = get_driver(olt.olt_model)
    ports = db.query(OLTPort).filter(OLTPort.olt_id == olt_id).all()

    if not ports:
        raise HTTPException(
            status_code=404,
            detail="Nenhuma porta PON descoberta. Execute a descoberta primeiro."
        )
    if not serial:
        raise HTTPException(status_code=400, detail="Informe o número de série (serial)")

    results = []
    try:
        client = get_olt_client(olt.ip, olt.port, olt.username, olt.password, olt.protocol)
        client.connect()

        for p in ports:
            iface = driver.olt_iface(p.slot, p.card, p.pon)
            out = client.execute_command(driver.cmd_onu_baseinfo(iface), timeout=20)
            onus = driver.parse_onu_baseinfo(out)
            for onu in onus:
                if serial.upper() in onu.get("serial", "").upper():
                    onu["slot"]          = p.slot
                    onu["card"]          = p.card
                    onu["pon"]           = p.pon
                    onu["olt_interface"] = iface
                    results.append(onu)

        client.disconnect()
    except OLTConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e))

    return {"results": results, "total": len(results), "serial_searched": serial}


@router.post("/{olt_id}/pon/{slot}/{card}/{pon}/onu/{onu_id}/reboot")
def onu_reboot(
    olt_id: int,
    slot: int,
    card: int,
    pon: int,
    onu_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Reinicia uma ONU específica.
    Funciona para ZTE C320 e ZTE C300/C610 (Titan).
    Requer perfil admin.
    """
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Apenas administradores podem reiniciar ONUs")

    olt = _get_olt_or_404(olt_id, db)
    driver = get_driver(olt.olt_model)
    onu_ref = driver.onu_iface(f"{slot}/{card}/{pon}:{onu_id}")
    logger.info(f"[REBOOT] Solicitado por {current_user.username}: {onu_ref} em {olt.ip}")

    try:
        result = reboot_onu(
            olt.ip, olt.port, olt.username, olt.password, olt.protocol,
            slot, card, pon, onu_id, driver=driver
        )
        return result
    except OLTConnectionError as e:
        logger.error(f"[REBOOT] Erro de conexão: {e}")
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error(f"[REBOOT] Erro inesperado: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Erro interno: {str(e)}")


@router.get("/{olt_id}/pon/{slot}/{card}/{pon}/onu/{onu_id}/traffic")
def get_onu_traffic_endpoint(
    olt_id: int,
    slot: int,
    card: int,
    pon: int,
    onu_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Retorna tráfego em tempo real de uma ONU (rx/tx Bps, pps, utilização).
    Sem cache — sempre consulta a OLT diretamente para dados em tempo real.
    """
    olt = _get_olt_or_404(olt_id, db)
    driver = get_driver(olt.olt_model)

    try:
        result = get_onu_traffic(
            olt.ip, olt.port, olt.username, olt.password, olt.protocol,
            slot, card, pon, onu_id, driver=driver
        )
        result["olt_id"] = olt_id
        result["last_updated"] = _now_iso()
        return result
    except OLTConnectionError as e:
        logger.error(f"[TRAFFIC] Erro de conexão: {e}")
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error(f"[TRAFFIC] Erro inesperado: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Erro interno: {str(e)}")


@router.delete("/{olt_id}/cache")
def clear_olt_cache(
    olt_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Limpa todo o cache Redis de uma OLT."""
    _get_olt_or_404(olt_id, db)
    deleted = cache.delete_pattern(f"olt:{olt_id}:*")
    return {"message": f"Cache limpo: {deleted} chave(s) removida(s)"}


@router.delete("/{olt_id}/pon/{slot}/{card}/{pon}/cache")
def clear_pon_cache(
    olt_id: int,
    slot: int,
    card: int,
    pon: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Limpa o cache Redis de uma porta PON específica."""
    _get_olt_or_404(olt_id, db)
    deleted = cache.delete_pattern(f"olt:{olt_id}:pon:{slot}:{card}:{pon}:*")
    deleted += cache.delete_pattern(f"olt:{olt_id}:onu:{slot}:{card}:{pon}:*")
    return {"message": f"Cache da PON limpo: {deleted} chave(s) removida(s)"}
