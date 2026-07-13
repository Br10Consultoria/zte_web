"""
Rotas de consulta de ONUs.
Suporta múltiplos modelos via olt_driver.py:
  zte_c320 — ZTE C320/C600/C610/C620/C650 (gpon-olt_1/CARD/PON)
  zte_c300 — ZTE C300/C300M/C300T Titan   (gpon_olt-SLOT/CARD/PON)
"""
import logging
import re
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
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
from ..models import User, OLT, OLTPort, ProvisionTemplate, ONUAnnotation
from ..auth import get_current_user
from ..olt_client import (
    get_olt_client, OLTConnectionError,
    parse_onu_power, parse_onu_detail, get_onu_full_details,
    reboot_onu, get_onu_traffic
)
from ..olt_driver import get_driver
from ..redis_client import cache
from ..olt_status_cache import collect_pon_status, pon_status_cache_key

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


def _annotation_dict(annotation: Optional[ONUAnnotation]) -> dict:
    if not annotation:
        return {"operation_mode": "auto", "comment": ""}
    return {
        "operation_mode": annotation.operation_mode or "auto",
        "comment": annotation.comment or "",
    }


def _get_annotation(db: Session, olt_id: int, slot: int, card: int, pon: int, onu_id: int) -> Optional[ONUAnnotation]:
    return db.query(ONUAnnotation).filter(
        ONUAnnotation.olt_id == olt_id,
        ONUAnnotation.slot == slot,
        ONUAnnotation.card == card,
        ONUAnnotation.pon == pon,
        ONUAnnotation.onu_id == onu_id,
    ).first()


def _signal_history_key(olt_id: int, slot: int, card: int, pon: int, onu_id: int) -> str:
    return f"olt:{olt_id}:onu:{slot}:{card}:{pon}:{onu_id}:signal_history"


def _record_signal_history(olt_id: int, slot: int, card: int, pon: int, onu_id: int, result: dict) -> list:
    power = result.get("power") or {}
    rx_power = power.get("rx_power")
    if rx_power is None:
        rx_power = power.get("onu_rx_power")
    olt_rx_power = power.get("olt_rx_power")
    key = _signal_history_key(olt_id, slot, card, pon, onu_id)
    history = cache.get(key) or []
    cutoff = datetime.now() - timedelta(days=30)

    pruned = []
    for item in history:
        try:
            ts = datetime.fromisoformat(str(item.get("timestamp")))
        except Exception:
            continue
        if ts >= cutoff:
            pruned.append(item)

    if rx_power is not None or olt_rx_power is not None:
        pruned.append({
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "label": _now_iso(),
            "rx_power": rx_power,
            "olt_rx_power": olt_rx_power,
            "rx_status": power.get("rx_status") or power.get("onu_rx_status"),
        })

    cache.set(key, pruned, ttl=30 * 24 * 3600)
    return pruned


def _template_dict(tpl: ProvisionTemplate) -> dict:
    return {
        "id": tpl.id,
        "name": tpl.name,
        "model_alias": tpl.model_alias,
        "vlan": tpl.vlan,
        "onu_type": tpl.onu_type,
        "start_onu_number": tpl.start_onu_number,
        "commands": tpl.commands,
        "is_active": tpl.is_active,
    }


def _safe_cli_name(value: str) -> str:
    value = (value or "CLIENTE").strip()
    value = re.sub(r"\s+", "_", value)
    return re.sub(r"[^A-Za-z0-9_.-]", "", value)[:64] or "CLIENTE"


def _render_template(commands: str, values: dict) -> list:
    cli_name = _safe_cli_name(values.get("cli_name", "CLIENTE"))
    rendered = commands.replace("FND_TEXT([*CLI_NOME])", cli_name)
    replacements = {
        "[*PORT_NAME]": values["port_name"],
        "[*ONU_NUMBER]": str(values["onu_number"]),
        "[ONU_SERIAL]": values["serial"],
        "[*ONU_TYPE]": values.get("onu_type") or "ZTE-F601",
        "[*PORT_VLAN]": str(values["vlan"]),
        "[*CLI_NOME]": cli_name,
    }
    for token, value in replacements.items():
        rendered = rendered.replace(token, value)
    return [
        line.strip()
        for line in rendered.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


@router.get("/provision-templates")
def list_provision_templates(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    templates = db.query(ProvisionTemplate).order_by(ProvisionTemplate.name).all()
    return [_template_dict(t) for t in templates]


@router.post("/provision-templates")
def create_provision_template(
    body: dict = Body(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Apenas administradores podem criar templates")
    tpl = ProvisionTemplate(
        name=body.get("name") or "Template",
        model_alias=body.get("model_alias") or "",
        vlan=int(body.get("vlan") or 1),
        onu_type=body.get("onu_type") or "ZTE-F601",
        start_onu_number=int(body.get("start_onu_number") or 1),
        commands=body.get("commands") or "",
        is_active=bool(body.get("is_active", True)),
    )
    db.add(tpl)
    db.commit()
    db.refresh(tpl)
    return _template_dict(tpl)


@router.put("/provision-templates/{template_id}")
def update_provision_template(
    template_id: int,
    body: dict = Body(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Apenas administradores podem editar templates")
    tpl = db.query(ProvisionTemplate).filter(ProvisionTemplate.id == template_id).first()
    if not tpl:
        raise HTTPException(status_code=404, detail="Template nao encontrado")
    for field in ("name", "model_alias", "onu_type", "commands"):
        if field in body:
            setattr(tpl, field, body.get(field) or "")
    for field in ("vlan", "start_onu_number"):
        if field in body:
            setattr(tpl, field, int(body.get(field) or 1))
    if "is_active" in body:
        tpl.is_active = bool(body.get("is_active"))
    db.commit()
    db.refresh(tpl)
    return _template_dict(tpl)


@router.delete("/provision-templates/{template_id}")
def delete_provision_template(
    template_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Apenas administradores podem remover templates")
    tpl = db.query(ProvisionTemplate).filter(ProvisionTemplate.id == template_id).first()
    if not tpl:
        raise HTTPException(status_code=404, detail="Template nao encontrado")
    db.delete(tpl)
    db.commit()
    return {"success": True}


@router.post("/{olt_id}/provision")
def provision_onu(
    olt_id: int,
    body: dict = Body(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Apenas administradores podem provisionar ONUs")
    olt = _get_olt_or_404(olt_id, db)
    tpl = db.query(ProvisionTemplate).filter(ProvisionTemplate.id == int(body.get("template_id"))).first()
    if not tpl:
        raise HTTPException(status_code=404, detail="Template nao encontrado")

    values = {
        "port_name": body.get("port_name") or body.get("onu_index") or "",
        "onu_number": int(body.get("onu_number") or tpl.start_onu_number or 1),
        "serial": body.get("serial") or "",
        "vlan": int(body.get("vlan") or tpl.vlan or 1),
        "onu_type": body.get("onu_type") or tpl.onu_type or body.get("model") or "ZTE-F601",
        "cli_name": body.get("cli_name") or body.get("serial") or "CLIENTE",
    }
    if not values["port_name"] or not values["serial"]:
        raise HTTPException(status_code=400, detail="Informe porta e serial da ONU")

    commands = _render_template(tpl.commands, values)
    client = None
    try:
        client = get_olt_client(olt.ip, olt.port, olt.username, olt.password, olt.protocol)
        client.connect()
        outputs = []
        for cmd in commands:
            outputs.append(client.execute_command(cmd, timeout=45))
        cache.delete(f"olt:{olt_id}:uncfg")
        cache.delete_pattern(f"olt:{olt_id}:pon:*:status")
        return {
            "success": True,
            "message": f"Provisionamento enviado para {values['serial']}",
            "commands": commands,
            "output": "\n".join(outputs)[-6000:],
        }
    except OLTConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error(f"[PROVISION] Erro inesperado: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Erro interno: {str(e)}")
    finally:
        if client:
            try:
                client.disconnect()
            except Exception:
                pass


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
    Cache Redis por 1 hora. Use force_refresh=true para atualizar.
    """
    olt = _get_olt_or_404(olt_id, db)
    port_obj = _get_port(olt_id, slot, card, pon, db)
    driver = get_driver(olt.olt_model)

    cache_key = pon_status_cache_key(olt_id, slot, card, pon)

    if not force_refresh:
        cached_data = cache.get(cache_key)
        if cached_data:
            if cached_data.get("details_included"):
                cached_data["cached"] = True
                cache_info = cache.get_cache_info(cache_key)
                cached_data["cache_expires_in"] = cache_info.get("expires_in")
                return cached_data
            logger.info(f"[PON_STATUS] Cache sem detalhes para {cache_key}; atualizando para preencher uptime")

    iface = driver.olt_iface(slot, card, pon)
    logger.info(f"[PON_STATUS] Consultando {iface} na OLT {olt.ip} (modelo: {olt.olt_model})")

    client = None
    try:
        client = get_olt_client(olt.ip, olt.port, olt.username, olt.password, olt.protocol)
        client.connect()

        if not port_obj:
            port_obj = OLTPort(olt_id=olt_id, slot=slot, card=card, pon=pon)

        result = collect_pon_status(client, driver, olt, port_obj, include_details=True)
        # Atualiza contagem na porta
        if port_obj:
            port_obj.onu_count = result["total"]
            port_obj.status = "online" if result["online"] > 0 else "active"
            db.commit()

        cache.set(cache_key, result)
        return result

    except OLTConnectionError as e:
        logger.error(f"[PON_STATUS] Erro de conexão: {e}")
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error(f"[PON_STATUS] Erro inesperado: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Erro interno: {str(e)}")
    finally:
        if client:
            try:
                client.disconnect()
            except Exception:
                pass


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
            cached_data["annotation"] = _annotation_dict(_get_annotation(db, olt_id, slot, card, pon, onu_id))
            cached_data["signal_history"] = cache.get(_signal_history_key(olt_id, slot, card, pon, onu_id)) or []
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
        result["annotation"] = _annotation_dict(_get_annotation(db, olt_id, slot, card, pon, onu_id))
        result["signal_history"] = _record_signal_history(olt_id, slot, card, pon, onu_id, result)

        detail = result.get("detail") or {}
        history = detail.get("history") or []
        if history and not detail.get("last_down_cause"):
            last_event = history[-1]
            detail["last_down_cause"] = last_event.get("cause")
            detail["last_offline_time"] = last_event.get("offline_time")
        if result.get("status") and result["status"].get("last_down_cause") and not detail.get("last_down_cause"):
            detail["last_down_cause"] = result["status"].get("last_down_cause")
        result["detail"] = detail

        cache.set(cache_key, result)
        return result

    except OLTConnectionError as e:
        logger.error(f"[ONU_FULL] Erro de conexão: {e}")
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error(f"[ONU_FULL] Erro inesperado: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Erro interno: {str(e)}")


@router.put("/{olt_id}/pon/{slot}/{card}/{pon}/onu/{onu_id}/annotation")
def save_onu_annotation(
    olt_id: int,
    slot: int,
    card: int,
    pon: int,
    onu_id: int,
    body: dict = Body(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Salva modo de operacao e comentario manual da ONU."""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Apenas administradores podem editar anotacoes")
    _get_olt_or_404(olt_id, db)
    mode = (body.get("operation_mode") or "auto").strip().lower()
    if mode not in ("auto", "bridge", "router"):
        raise HTTPException(status_code=400, detail="Modo de operacao invalido")

    annotation = _get_annotation(db, olt_id, slot, card, pon, onu_id)
    if not annotation:
        annotation = ONUAnnotation(
            olt_id=olt_id,
            slot=slot,
            card=card,
            pon=pon,
            onu_id=onu_id,
            created_at=datetime.utcnow(),
        )
        db.add(annotation)
    annotation.operation_mode = mode
    annotation.comment = (body.get("comment") or "").strip()
    annotation.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(annotation)
    cache.delete(f"olt:{olt_id}:onu:{slot}:{card}:{pon}:{onu_id}:full")
    return _annotation_dict(annotation)


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
        out = client.execute_command("show pon onu uncfg", timeout=30)
        if "%Error" in out or "Invalid" in out or "Unknown" in out:
            logger.warning("[UNCFG] show pon onu uncfg falhou; tentando comando legado")
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
    serial: Optional[str] = Query(None, description="Numero de serie da ONU"),
    model: Optional[str] = Query(None, description="Modelo da ONU"),
    slot: Optional[int] = Query(None, description="Slot da porta PON"),
    card: Optional[int] = Query(None, description="Card/subslot da porta PON"),
    pon: Optional[int] = Query(None, description="Numero da porta PON"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Busca ONUs por serial, modelo e/ou porta PON."""
    olt = _get_olt_or_404(olt_id, db)
    driver = get_driver(olt.olt_model)

    ports_query = db.query(OLTPort).filter(OLTPort.olt_id == olt_id)
    if slot is not None:
        ports_query = ports_query.filter(OLTPort.slot == slot)
    if card is not None:
        ports_query = ports_query.filter(OLTPort.card == card)
    if pon is not None:
        ports_query = ports_query.filter(OLTPort.pon == pon)
    ports = ports_query.all()

    if not ports:
        raise HTTPException(
            status_code=404,
            detail="Nenhuma porta PON descoberta. Execute a descoberta primeiro."
        )

    serial_filter = (serial or "").strip().upper()
    model_filter = (model or "").strip().upper()
    if not serial_filter and not model_filter and pon is None:
        raise HTTPException(status_code=400, detail="Informe serial, modelo ou porta PON para buscar")

    results = []
    client = None
    try:
        client = get_olt_client(olt.ip, olt.port, olt.username, olt.password, olt.protocol)
        client.connect()

        for p in ports:
            iface = driver.olt_iface(p.slot, p.card, p.pon)
            out = client.execute_command(driver.cmd_onu_baseinfo(iface), timeout=20)
            onus = driver.parse_onu_baseinfo(out)
            for onu in onus:
                onu_serial = (onu.get("serial") or "").upper()
                onu_model = (onu.get("model") or "").upper()
                if serial_filter and serial_filter not in onu_serial:
                    continue
                if model_filter and model_filter not in onu_model:
                    continue
                onu["slot"]          = p.slot
                onu["card"]          = p.card
                onu["pon"]           = p.pon
                onu["port"]          = p.pon
                onu["olt_interface"] = iface
                results.append(onu)
    except OLTConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e))
    finally:
        if client:
            client.disconnect()

    return {
        "results": results,
        "total": len(results),
        "serial_searched": serial,
        "model_searched": model,
        "slot": slot,
        "card": card,
        "pon": pon,
    }

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


@router.delete("/{olt_id}/pon/{slot}/{card}/{pon}/onu/{onu_id}")
def onu_remove(
    olt_id: int,
    slot: int,
    card: int,
    pon: int,
    onu_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Remove uma ONU provisionada da interface PON.
    Sequencia ZTE: configure terminal -> interface OLT -> no onu ID -> write.
    Requer perfil admin.
    """
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Apenas administradores podem remover ONUs")

    olt = _get_olt_or_404(olt_id, db)
    driver = get_driver(olt.olt_model)
    olt_iface = driver.olt_iface(slot, card, pon)
    logger.warning(f"[REMOVE_ONU] Solicitado por {current_user.username}: {olt_iface} onu {onu_id} em {olt.ip}")

    client = None
    try:
        client = get_olt_client(olt.ip, olt.port, olt.username, olt.password, olt.protocol)
        client.connect()
        commands = [
            "configure terminal",
            f"interface {olt_iface}",
            f"no onu {onu_id}",
            "exit",
            "exit",
            "write",
        ]
        outputs = []
        for cmd in commands:
            outputs.append(client.execute_command(cmd, timeout=30))

        cache.delete(pon_status_cache_key(olt_id, slot, card, pon))
        cache.delete(f"olt:{olt_id}:onu:{slot}:{card}:{pon}:{onu_id}:full")
        cache.delete(f"olt:{olt_id}:onu:{slot}:{card}:{pon}:{onu_id}:detail")
        cache.delete(f"olt:{olt_id}:onu:{slot}:{card}:{pon}:{onu_id}:power")

        return {
            "success": True,
            "message": f"ONU {onu_id} removida de {olt_iface}.",
            "olt_interface": olt_iface,
            "onu_id": onu_id,
            "output": "\n".join(outputs)[-4000:],
        }
    except OLTConnectionError as e:
        logger.error(f"[REMOVE_ONU] Erro de conexao: {e}")
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error(f"[REMOVE_ONU] Erro inesperado: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Erro interno: {str(e)}")
    finally:
        if client:
            try:
                client.disconnect()
            except Exception:
                pass


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
