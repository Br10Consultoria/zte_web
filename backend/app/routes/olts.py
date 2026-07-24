"""
Rotas de gerenciamento de OLTs.
Suporta múltiplos modelos via olt_driver.py:
  zte_c320 — ZTE C320/C600/C610/C620/C650 (gpon-olt_1/CARD/PON)
  zte_c300 — ZTE C300/C300M/C300T Titan   (gpon_olt-SLOT/CARD/PON)
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from datetime import datetime
from typing import List

from ..database import get_db
from ..models import User, OLT, OLTPort
from ..schemas import OLTCreate, OLTUpdate, OLTResponse, OLTPortResponse
from ..auth import get_current_user, get_current_admin
from ..olt_client import (
    OLTConnectionError, get_olt_client, parse_software_version
)
from ..olt_driver import get_driver, OLT_MODELS, detect_model
from ..snmp_client import (
    snmp_discover_pon_ports, snmp_get_system_info, snmp_test_connection, SNMPError
)
from ..redis_client import cache
from ..olt_status_cache import refresh_olt_ports_status


router = APIRouter(prefix="/olts", tags=["OLTs"])
logger = logging.getLogger("routes.olts")


def _normalize_olt_model(model: str = None) -> str:
    return "zte_c600" if model in (None, "", "zte_c320") else model


# ============================================================
# CRUD
# ============================================================

@router.get("", response_model=List[OLTResponse])
def list_olts(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    return db.query(OLT).all()


@router.post("", response_model=OLTResponse, status_code=201)
def create_olt(
    body: OLTCreate,
    current_user: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    olt = OLT(
        name=body.name,
        ip=body.ip,
        port=body.port,
        username=body.username,
        password=body.password,
        protocol=body.protocol,
        snmp_community=body.snmp_community or "public",
        snmp_version=body.snmp_version or "2c",
        olt_model=_normalize_olt_model(body.olt_model),
        status="unknown"
    )
    db.add(olt)
    db.commit()
    db.refresh(olt)
    return olt


@router.get("/models")
def list_olt_models():
    """Retorna a lista de modelos de OLT suportados."""
    return [
        {"key": key, "label": info["label"], "vendor": info["vendor"]}
        for key, info in OLT_MODELS.items()
    ]


@router.get("/{olt_id}", response_model=OLTResponse)
def get_olt(
    olt_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    olt = db.query(OLT).filter(OLT.id == olt_id).first()
    if not olt:
        raise HTTPException(status_code=404, detail="OLT não encontrada")
    return olt


@router.put("/{olt_id}", response_model=OLTResponse)
def update_olt(
    olt_id: int,
    body: OLTUpdate,
    current_user: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    olt = db.query(OLT).filter(OLT.id == olt_id).first()
    if not olt:
        raise HTTPException(status_code=404, detail="OLT não encontrada")

    for field, value in body.model_dump(exclude_none=True).items():
        if field == "olt_model":
            value = _normalize_olt_model(value)
        setattr(olt, field, value)

    db.commit()
    db.refresh(olt)
    cache.delete_pattern(f"olt:{olt_id}:*")
    return olt


@router.delete("/{olt_id}")
def delete_olt(
    olt_id: int,
    current_user: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    olt = db.query(OLT).filter(OLT.id == olt_id).first()
    if not olt:
        raise HTTPException(status_code=404, detail="OLT não encontrada")

    db.delete(olt)
    db.commit()
    cache.delete_pattern(f"olt:{olt_id}:*")
    return {"message": "OLT excluída com sucesso"}


# ============================================================
# CONEXÃO E DESCOBERTA
# ============================================================

@router.post("/{olt_id}/test-connection")
def test_connection(
    olt_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Testa conectividade com a OLT via SNMP e SSH/Telnet."""
    olt = db.query(OLT).filter(OLT.id == olt_id).first()
    if not olt:
        raise HTTPException(status_code=404, detail="OLT não encontrada")

    community = olt.snmp_community or "public"
    snmp_version = olt.snmp_version or "2c"
    details = {}

    # Testa SNMP
    snmp_ok, snmp_output = snmp_test_connection(olt.ip, community, 161, snmp_version)
    if snmp_ok:
        details["snmp"] = "ok"
        details["snmp_info"] = snmp_output[:500]
        info = snmp_get_system_info(olt.ip, community, 161, snmp_version)
        if info.get("model"):
            olt.model = info["model"]
        if info.get("firmware"):
            olt.firmware = info["firmware"]
    else:
        details["snmp"] = f"indisponível: {snmp_output}"

    # Testa SSH/Telnet
    from ..olt_client import test_olt_connection
    ssh_ok, ssh_output = test_olt_connection(
        olt.ip, olt.port, olt.username, olt.password, olt.protocol, olt.olt_model
    )
    if ssh_ok:
        details["ssh_telnet"] = "ok"
        info = parse_software_version(ssh_output)
        if info.get("firmware") and not olt.firmware:
            olt.firmware = info["firmware"]
        if info.get("model") and not olt.model:
            olt.model = info["model"]
        # Tenta auto-detectar o modelo pelo banner de login
        # Sempre atualiza se detectado (permite corrigir modelo errado)
        detected = detect_model(ssh_output)
        if detected:
            details["detected_model"] = detected
            if not olt.olt_model or olt.olt_model in ("zte_c320", "zte_c600"):
                # Atualiza apenas se ainda não foi definido manualmente como c300
                olt.olt_model = detected
                details["model_auto_set"] = True
    else:
        details["ssh_telnet"] = f"falhou: {ssh_output[:200]}"

    success = snmp_ok or ssh_ok
    olt.status = "online" if success else "offline"
    olt.last_check = datetime.now()
    db.commit()

    return {
        "success": success,
        "status": olt.status,
        "snmp_available": snmp_ok,
        "ssh_telnet_available": ssh_ok,
        "olt_model": olt.olt_model,
        "details": details,
        "message": "Conexão estabelecida com sucesso!" if success else "Falha na conexão"
    }


@router.post("/{olt_id}/discover")
def discover_ports(
    olt_id: int,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """
    Descobre as portas PON da OLT.
    Usa o driver correto para o modelo cadastrado.
    Tenta SNMP primeiro, depois SSH/Telnet como fallback.
    """
    olt = db.query(OLT).filter(OLT.id == olt_id).first()
    if not olt:
        raise HTTPException(status_code=404, detail="OLT não encontrada")

    driver = get_driver(olt.olt_model)
    community = olt.snmp_community or "public"
    snmp_version = olt.snmp_version or "2c"
    discovery_method = "snmp"
    ports = []
    error_msgs = []

    logger.info(f"[DISCOVER] OLT {olt.name} ({olt.ip}) — modelo: {olt.olt_model or 'zte_c600'}")

    # --- Tentativa 1: SNMP ---
    try:
        logger.info(f"[DISCOVER] Tentando SNMP com community '{community}'")
        snmp_ports = snmp_discover_pon_ports(
            olt.ip, community, 161, snmp_version,
            ssh_port=olt.port,
            ssh_username=olt.username,
            ssh_password=olt.password,
            ssh_protocol=olt.protocol,
            olt_model=olt.olt_model or "zte_c600"
        )
        if snmp_ports:
            ports = snmp_ports
            logger.info(f"[DISCOVER] SNMP encontrou {len(ports)} portas")
            info = snmp_get_system_info(olt.ip, community, 161, snmp_version)
            if info.get("model"):
                olt.model = info["model"]
            if info.get("firmware"):
                olt.firmware = info["firmware"]
        else:
            logger.warning("[DISCOVER] SNMP retornou 0 portas, tentando SSH/Telnet")
            discovery_method = "ssh_telnet"
    except (SNMPError, Exception) as e:
        error_msgs.append(f"SNMP: {e}")
        discovery_method = "ssh_telnet"
        logger.warning(f"[DISCOVER] SNMP falhou: {e}")

    # --- Tentativa 2: SSH/Telnet com driver específico ---
    if not ports:
        try:
            logger.info(f"[DISCOVER] Tentando SSH/Telnet ({olt.protocol}) em {olt.ip}:{olt.port}")
            client = get_olt_client(
                olt.ip, olt.port, olt.username, olt.password, olt.protocol, olt.olt_model
            )
            client.connect()

            # Estratégia 1: listar portas que já têm ONUs ativas via 'show gpon onu state'
            # ATENÇÃO: este comando só retorna portas COM ONUs — portas vazias não aparecem.
            # Por isso, sempre complementamos com varredura porta a porta (Estratégia 2).
            for cmd in driver.cmd_discover_ports():
                logger.info(f"[DISCOVER] Tentando: {cmd}")
                output = client.execute_command(cmd, timeout=20)
                found = driver.parse_discover_ports(output)
                for p in found:
                    key = (p["slot"], p["card"], p["pon"])
                    if key not in {(x["slot"], x.get("card", 1), x["pon"]) for x in ports}:
                        ports.append(p)
                if ports:
                    logger.info(f"[DISCOVER] Estratégia 1 encontrou {len(ports)} portas com ONUs via '{cmd}'")
                    break

            # Estratégia 2: varredura porta a porta para encontrar TODAS as portas,
            # incluindo as que estão vazias (sem ONUs ativas).
            # Sempre executada para complementar a Estratégia 1.
            # Para C300/C610: slot=1..2, card=1..8, pon=1..32
            # Para C320:      slot=1 (fixo), card=1..8, pon=1..32
            logger.info("[DISCOVER] Iniciando varredura porta a porta para descobrir portas vazias")
            seen = {(p["slot"], p.get("card", 1), p["pon"]) for p in ports}  # já encontradas
            model_key = getattr(driver, 'model_key', '')
            is_c300 = model_key == 'zte_c300'
            is_parks = model_key == 'parks_3000_4000'
            slot_range = range(1, 2) if is_parks else (range(1, 3) if is_c300 else range(1, 2))
            card_range = range(1, 2) if is_parks else range(1, 9)
            pon_range = range(1, 17) if is_parks else range(1, 33)
            consecutive_empty = 0  # para parar cedo quando não há mais placas
            for slot in slot_range:
                for card in card_range:
                    card_has_any = False
                    for pon in pon_range:
                        key = (slot, card, pon)
                        if key in seen:
                            card_has_any = True
                            continue
                        iface = driver.olt_iface(slot, card, pon)
                        try:
                            out = client.execute_command(
                                driver.cmd_onu_state(iface), timeout=8
                            )
                            if driver.parse_onu_state_for_discover(out, slot, card, pon):
                                seen.add(key)
                                card_has_any = True
                                ports.append({
                                    "slot": slot, "card": card, "pon": pon,
                                    "port_type": "gpon",
                                    "description": iface
                                })
                                logger.info(f"[DISCOVER] Porta encontrada: {iface}")
                            else:
                                logger.debug(f"[DISCOVER] {iface}: sem ONUs ou porta inválida")
                        except Exception as ex:
                            logger.debug(f"[DISCOVER] {iface} inválida: {ex}")
                    # Se nenhuma PON da placa respondeu, incrementa contador de placas vazias
                    if not card_has_any:
                        consecutive_empty += 1
                        if consecutive_empty >= 2:
                            logger.info(f"[DISCOVER] 2 placas consecutivas sem portas (card={card}), encerrando varredura")
                            break
                    else:
                        consecutive_empty = 0

            client.disconnect()
            logger.info(f"[DISCOVER] SSH/Telnet encontrou {len(ports)} portas")

        except OLTConnectionError as e:
            error_msgs.append(f"SSH/Telnet: {e}")
            raise HTTPException(
                status_code=503,
                detail=f"Descoberta falhou. Erros: {'; '.join(error_msgs)}"
            )

    if not ports:
        raise HTTPException(
            status_code=404,
            detail=f"Nenhuma porta PON encontrada. Erros: {'; '.join(error_msgs)}"
        )

    # Remove portas antigas e insere as novas
    try:
        db.query(OLTPort).filter(OLTPort.olt_id == olt_id).delete()
        db.flush()
        logger.info(f"[DISCOVER] Inserindo {len(ports)} portas")

        for p in ports:
            slot = p["slot"]
            card = p.get("card", 1)
            pon  = p["pon"]
            iface = driver.olt_iface(slot, card, pon)
            logger.debug(f"[DISCOVER] slot={slot} card={card} pon={pon} iface={iface}")
            port_obj = OLTPort(
                olt_id=olt_id,
                slot=slot,
                card=card,
                pon=pon,
                port_type=p.get("port_type", "gpon"),
                description=p.get("description", iface),
                status="unknown",
                onu_count=p.get("onu_count", 0)
            )
            db.add(port_obj)

        olt.status = "online"
        olt.last_check = datetime.now()
        db.commit()
        logger.info(f"[DISCOVER] {len(ports)} portas salvas com sucesso")
    except Exception as db_err:
        db.rollback()
        logger.error(f"[DISCOVER] Erro ao salvar portas: {db_err}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Erro ao salvar portas: {str(db_err)}")

    cache.delete_pattern(f"olt:{olt_id}:*")

    background_tasks.add_task(
        _update_ports_onu_count,
        olt_id, olt.ip, olt.port, olt.username, olt.password, olt.protocol, olt.olt_model
    )

    return {
        "message": f"Descoberta via {discovery_method.upper()}: {len(ports)} porta(s) PON encontrada(s).",
        "ports_found": len(ports),
        "discovery_method": discovery_method,
        "olt_model": olt.olt_model,
        "ports": [
            {
                "slot":      p["slot"],
                "card":      p.get("card", 1),
                "pon":       p["pon"],
                "interface": driver.olt_iface(p["slot"], p.get("card", 1), p["pon"]),
                "type":      p.get("port_type", "gpon"),
                "onu_count": p.get("onu_count", 0),
            }
            for p in ports
        ]
    }


def _update_ports_onu_count(olt_id: int, ip: str, port: int,
                             username: str, password: str, protocol: str,
                             olt_model: str = None):
    """
    Tarefa em background: conecta na OLT e atualiza contagem de ONUs por porta.
    Usa o driver correto para o modelo da OLT.
    """
    logger.info(f"[BG] Atualizando cache Redis de ONUs para OLT {olt_id} (modelo: {olt_model})")
    result = refresh_olt_ports_status(olt_id, include_details=False)
    if result.get("errors"):
        logger.warning(f"[BG] Atualizacao da OLT {olt_id} finalizada com erros: {result}")
    else:
        logger.info(f"[BG] Atualizacao da OLT {olt_id} concluida: {result}")


# ============================================================
# PORTAS E STATUS
# ============================================================

@router.get("/{olt_id}/ports", response_model=List[OLTPortResponse])
def get_olt_ports(
    olt_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    olt = db.query(OLT).filter(OLT.id == olt_id).first()
    if not olt:
        raise HTTPException(status_code=404, detail="OLT não encontrada")

    ports = db.query(OLTPort).filter(OLTPort.olt_id == olt_id).order_by(
        OLTPort.slot, OLTPort.card, OLTPort.pon
    ).all()
    return ports


@router.post("/{olt_id}/refresh-status")
def refresh_olt_status_cache(
    olt_id: int,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Atualiza em background o cache Redis de status/ONUs de todas as portas da OLT."""
    olt = db.query(OLT).filter(OLT.id == olt_id).first()
    if not olt:
        raise HTTPException(status_code=404, detail="OLT nÃ£o encontrada")

    background_tasks.add_task(
        _update_ports_onu_count,
        olt_id, olt.ip, olt.port, olt.username, olt.password, olt.protocol, olt.olt_model
    )
    return {
        "message": "Atualizacao de status iniciada em background",
        "olt_id": olt_id,
    }


@router.get("/{olt_id}/status")
def get_olt_full_status(
    olt_id: int,
    force_refresh: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Retorna status geral da OLT com informações de hardware."""
    olt = db.query(OLT).filter(OLT.id == olt_id).first()
    if not olt:
        raise HTTPException(status_code=404, detail="OLT não encontrada")

    cache_key = cache.key_olt_status(olt_id)

    if not force_refresh:
        cached = cache.get(cache_key)
        if cached:
            cached["cached"] = True
            return cached

    try:
        client = get_olt_client(
            olt.ip, olt.port, olt.username, olt.password, olt.protocol, olt.olt_model
        )
        client.connect()

        result = {"olt_id": olt_id, "name": olt.name, "ip": olt.ip}

        out = client.execute_command("show software")
        if "%Error" in out or "Invalid" in out or "Unknown" in out:
            out = client.execute_command("show version")
        result["software"] = out[:1000]

        out = client.execute_command("show uptime")
        if "%Error" in out or "Invalid" in out or "Unknown" in out:
            out = client.execute_command("show interface gpon1/1")
        result["uptime"] = out[:500]

        client.disconnect()

        cache.set(cache_key, result)
        result["cached"] = False
        return result

    except OLTConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e))
