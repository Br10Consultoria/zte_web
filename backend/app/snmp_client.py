"""
SNMP Client para ZTE Titan — descoberta de portas PON via snmpwalk/snmpget do sistema.

A OLT ZTE C320/C600 retorna ifDescr vazio para interfaces PON.
Usa a OID proprietária ZTE para descobrir as portas:
  1.3.6.1.4.1.3902.1012.3.13.1.1.1  — nome da porta PON (ex: "OLT-1")
  1.3.6.1.4.1.3902.1012.3.13.1.1.13 — contagem de ONUs por porta
  1.3.6.1.2.1.2.2.1.8               — ifOperStatus (1=up, 2=down)
  1.3.6.1.2.1.1.1.0                 — sysDescr (modelo/firmware)

O ifIndex ZTE codifica slot e pon:
  slot = (if_index - base) // 65536 + 1
  pon  = ((if_index - base) % 65536) // 256 + 1

O campo 'card' (subslot) NÃO é detectável via SNMP na ZTE C320.
Ele é detectado via SSH/Telnet após a descoberta SNMP.
"""
import subprocess
import re
import logging
from typing import List, Dict, Tuple, Optional

logger = logging.getLogger("snmp_client")

# Constantes ZTE
ZTE_SLOT_STEP = 65536   # 0x10000
ZTE_PON_STEP  = 256     # 0x100

# OIDs
OID_ZTE_PON_NAME      = "1.3.6.1.4.1.3902.1012.3.13.1.1.1"
OID_ZTE_PON_ONU_COUNT = "1.3.6.1.4.1.3902.1012.3.13.1.1.13"
OID_IF_OPER_STATUS    = "1.3.6.1.2.1.2.2.1.8"
OID_SYS_DESCR         = "1.3.6.1.2.1.1.1.0"
OID_SYS_NAME          = "1.3.6.1.2.1.1.5.0"


class SNMPError(Exception):
    pass


def _find_snmp_tool(name: str) -> Optional[str]:
    """Localiza o binário snmpwalk/snmpget no PATH do sistema."""
    import shutil
    path = shutil.which(name)
    if path:
        return path
    for candidate in [f"/usr/bin/{name}", f"/usr/local/bin/{name}"]:
        import os
        if os.path.isfile(candidate):
            return candidate
    return None


def _decode_zte_index(if_index: int, base: int) -> Tuple[int, int]:
    """
    Converte ifIndex ZTE para (slot, pon) usando a base detectada.
    Na ZTE C320/C600, o formato da CLI é gpon-olt_RACK/SLOT/PON onde RACK=1 fixo.
    O ifIndex codifica o SLOT (placa) e a PON:
      slot = (if_index - base) // 65536 + 1  → número da placa (1, 2, ...)
      pon  = ((if_index - base) % 65536) // 256 + 1  → porta PON (1-16)
    """
    diff = if_index - base
    slot = diff // ZTE_SLOT_STEP + 1
    pon  = (diff % ZTE_SLOT_STEP) // ZTE_PON_STEP + 1
    return slot, pon


def _encode_zte_index(slot: int, pon: int, base: int) -> int:
    """Converte (slot, pon) para ifIndex ZTE."""
    return base + (slot - 1) * ZTE_SLOT_STEP + (pon - 1) * ZTE_PON_STEP


def _snmp_walk(host: str, community: str, oid: str, port: int = 161,
               version: str = "2c", timeout: int = 15) -> List[Tuple[int, str]]:
    """
    Executa snmpwalk via subprocess.
    Retorna lista de (if_index_int, value_str).
    Extrai sempre o último número do OID como índice.
    """
    tool = _find_snmp_tool("snmpwalk")
    if not tool:
        raise SNMPError("snmpwalk não encontrado. Instale com: apt-get install -y snmp")

    cmd = [
        tool,
        "-v", version,
        "-c", community,
        "-t", str(timeout),
        "-r", "2",
        f"{host}:{port}",
        oid
    ]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout + 10
        )
    except subprocess.TimeoutExpired:
        raise SNMPError(f"Timeout ao executar snmpwalk em {host}:{port}")
    except FileNotFoundError:
        raise SNMPError("snmpwalk não encontrado. Instale com: apt-get install -y snmp")

    if result.returncode != 0 and not result.stdout.strip():
        raise SNMPError(f"snmpwalk falhou em {host}:{port}: {result.stderr.strip()[:200]}")

    rows = []
    for line in result.stdout.strip().split('\n'):
        line = line.strip()
        if not line or '=' not in line:
            continue

        parts = line.split('=', 1)
        if len(parts) != 2:
            continue

        full_oid = parts[0].strip()
        raw_val  = parts[1].strip()

        # Extrai o último número do OID como índice
        m = re.search(r'\.(\d+)\s*$', full_oid)
        if not m:
            continue
        idx = int(m.group(1))

        # Limpa o valor (remove tipo SNMP: STRING: "...", INTEGER: ..., etc.)
        val = raw_val
        for prefix in ["STRING:", "INTEGER:", "Gauge32:", "Counter32:", "OID:", "IpAddress:", "Timeticks:"]:
            if val.startswith(prefix):
                val = val[len(prefix):].strip()
                break
        val = val.strip('"').strip()

        rows.append((idx, val))

    return rows


def _snmp_get(host: str, community: str, oid: str, port: int = 161,
              version: str = "2c", timeout: int = 10) -> Optional[str]:
    """Executa snmpget e retorna o valor como string."""
    tool = _find_snmp_tool("snmpget")
    if not tool:
        return None

    cmd = [
        tool,
        "-v", version,
        "-c", community,
        "-t", str(timeout),
        "-r", "2",
        f"{host}:{port}",
        oid
    ]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout + 5
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None

        line = result.stdout.strip()
        if '=' not in line:
            return None

        val = line.split('=', 1)[1].strip()
        for prefix in ["STRING:", "INTEGER:", "Gauge32:", "OctetString:"]:
            if val.startswith(prefix):
                val = val[len(prefix):].strip()
                break
        return val.strip('"').strip()
    except Exception:
        return None


def snmp_test_connection(host: str, community: str, port: int = 161,
                         version: str = "2c") -> Tuple[bool, str]:
    """Testa a conectividade SNMP com a OLT."""
    try:
        val = _snmp_get(host, community, OID_SYS_DESCR, port, version)
        if val:
            return True, f"SNMP OK: {val[:100]}"
        return False, "SNMP não respondeu (community incorreta ou SNMP desabilitado)"
    except SNMPError as e:
        return False, str(e)
    except Exception as e:
        return False, f"Erro SNMP: {str(e)}"


def snmp_get_system_info(host: str, community: str, port: int = 161,
                         version: str = "2c") -> Dict:
    """Obtém informações do sistema via SNMP (modelo, firmware, hostname)."""
    result = {}
    try:
        descr = _snmp_get(host, community, OID_SYS_DESCR, port, version)
        if descr:
            result["sys_descr"] = descr
            m = re.search(r'(C\d{3,4})', descr)
            if m:
                result["model"] = f"ZTE {m.group(1)}"
            m = re.search(r'[Vv](\d+\.\d+[\.\d]*)', descr)
            if m:
                result["firmware"] = m.group(1)

        name = _snmp_get(host, community, OID_SYS_NAME, port, version)
        if name:
            result["hostname"] = name
    except Exception as e:
        logger.warning(f"[SNMP] Erro ao obter system info: {e}")

    return result


def _detect_card_via_ssh(ip: str, port: int, username: str, password: str,
                         protocol: str, slot: int, pon: int) -> int:
    """
    Detecta o card (subslot) real de uma porta PON via SSH/Telnet.
    Tenta cards 1, 2, 3, 4 e retorna o primeiro que responder.
    Nota: _olt_iface definida localmente para evitar import circular.
    """
    def _local_olt_iface(s: int, c: int, p: int) -> str:
        return f"gpon-olt_{s}/{c}/{p}"

    try:
        from .olt_client import get_olt_client, OLTConnectionError
        client = get_olt_client(ip, port, username, password, protocol)
        client.connect()

        for card in [1, 2, 3, 4]:
            iface = _local_olt_iface(slot, card, pon)
            try:
                out = client.execute_command(f"show gpon onu state {iface}", timeout=8)
                # Se não retornou erro, essa interface existe
                if (out.strip() and
                    "invalid" not in out.lower() and
                    "error" not in out.lower() and
                    "not exist" not in out.lower() and
                    "no such" not in out.lower() and
                    "%" not in out):
                    client.disconnect()
                    logger.info(f"[SNMP] Card detectado via SSH: slot={slot}, card={card}, pon={pon}")
                    return card
            except Exception:
                continue

        client.disconnect()
    except Exception as e:
        logger.warning(f"[SNMP] Falha ao detectar card via SSH para slot={slot}, pon={pon}: {e}")

    return 1  # Fallback para card=1


def snmp_discover_pon_ports(host: str, community: str, port: int = 161,
                            version: str = "2c",
                            ssh_port: int = None, ssh_username: str = None,
                            ssh_password: str = None, ssh_protocol: str = None) -> List[Dict]:
    """
    Descobre todas as portas PON via SNMP usando a OID proprietária ZTE.
    Detecta automaticamente a base do ifIndex a partir dos dados retornados.

    Se ssh_username for fornecido, detecta o card real via SSH para cada slot único.
    Caso contrário, usa card=1 como padrão (pode estar errado).
    """
    if not _find_snmp_tool("snmpwalk"):
        raise SNMPError(
            "snmpwalk não encontrado. Instale com: apt-get install -y snmp"
        )

    # Walk na OID proprietária ZTE para listar portas PON
    try:
        pon_rows = _snmp_walk(host, community, OID_ZTE_PON_NAME, port, version)
    except SNMPError:
        raise
    except Exception as e:
        raise SNMPError(f"Falha no SNMP walk ZTE PON em {host}:{port} — {e}")

    if not pon_rows:
        raise SNMPError(
            f"Nenhuma porta PON encontrada via SNMP em {host}:{port}. "
            f"Verifique a community string '{community}' e se o SNMP está habilitado na OLT."
        )

    # Filtra apenas índices grandes (> 1000) que são ifIndex ZTE reais
    valid_rows = [(idx, val) for idx, val in pon_rows if idx > 1000]

    if not valid_rows:
        raise SNMPError(
            f"Índices retornados não parecem ser ifIndex ZTE válidos: "
            f"{[r[0] for r in pon_rows[:5]]}"
        )

    # Detecta a base automaticamente: menor índice alinhado a 256
    all_indices = sorted([idx for idx, _ in valid_rows])

    # Verifica se a diferença entre índices consecutivos é múltipla de 256
    diffs = [all_indices[i+1] - all_indices[i] for i in range(min(len(all_indices)-1, 5))]
    step_ok = all(d % ZTE_PON_STEP == 0 for d in diffs)

    if step_ok:
        base = all_indices[0]
    else:
        base = all_indices[0] - (all_indices[0] % ZTE_SLOT_STEP)

    logger.info(f"[SNMP] Base detectada: {base}, total de portas: {len(valid_rows)}")

    # Decodifica todos os índices usando a base detectada
    pon_ports = []
    for if_index, val in valid_rows:
        try:
            slot, pon_num = _decode_zte_index(if_index, base)
        except Exception:
            continue

        # Valida: slot e pon devem ser razoáveis
        if not (1 <= slot <= 16 and 1 <= pon_num <= 16):
            continue

        # Formato CLI ZTE: gpon-olt_RACK/SLOT/PON onde RACK=1 fixo
        # slot aqui = número da placa (1=slot1, 2=slot2)
        # Na CLI: gpon-olt_1/1/1 (placa 1, porta 1) e gpon-olt_1/2/1 (placa 2, porta 1)
        pon_ports.append({
            "rack":      1,
            "slot":      slot,
            "card":      slot,   # card = slot da placa (compatível com banco)
            "pon":       pon_num,
            "if_index":  if_index,
            "if_name":   f"gpon-olt_1/{slot}/{pon_num}",  # Formato ZTE C320 (padrão)
            "port_type": "gpon",
            "description": val if val else f"OLT-{pon_num}",
            "status":    "unknown",
            "onu_count": 0,
            "_base":     base,
        })

    if not pon_ports:
        raise SNMPError(
            f"Nenhuma porta PON válida decodificada de {host}. "
            f"Base detectada: {base}. "
            f"Índices: {all_indices[:5]}"
        )

    # -------------------------------------------------------
    # Detecta o card real via SSH para cada slot único
    # -------------------------------------------------------
    # Formato correto ZTE: gpon-olt_1/SLOT/PON (rack=1 fixo, slot=número da placa)
    # Não é necessário detectar card via SSH — o slot JÁ é o número correto da placa
    # Exemplo: slot=1 → gpon-olt_1/1/1..16, slot=2 → gpon-olt_1/2/1..16
    logger.info(f"[SNMP] Usando formato ZTE correto: gpon-olt_1/SLOT/PON (rack=1 fixo)")
    for p in pon_ports:
        slot = p["slot"]
        pon  = p["pon"]
        p["card"] = slot   # card = slot da placa (para compatibilidade com o banco)
        p["if_name"] = f"gpon-olt_1/{slot}/{pon}"

    # Busca contagem de ONUs por porta via SNMP
    try:
        onu_rows = _snmp_walk(host, community, OID_ZTE_PON_ONU_COUNT, port, version)
        onu_map = {idx: val for idx, val in onu_rows if idx > 1000}
        for p in pon_ports:
            val = onu_map.get(p["if_index"], "0")
            try:
                p["onu_count"] = int(val) if str(val).isdigit() else 0
            except (ValueError, TypeError):
                p["onu_count"] = 0
    except Exception as e:
        logger.warning(f"[SNMP] Falha ao obter contagem de ONUs: {e}")

    # Busca status operacional via ifOperStatus
    try:
        oper_rows = _snmp_walk(host, community, OID_IF_OPER_STATUS, port, version)
        oper_map = {idx: val for idx, val in oper_rows}
        for p in pon_ports:
            oper = oper_map.get(p["if_index"], "0")
            try:
                oper_int = int(oper)
                if oper_int == 1:
                    p["status"] = "online"
                elif oper_int == 2:
                    p["status"] = "offline"
                else:
                    p["status"] = "active"
            except (ValueError, TypeError):
                p["status"] = "active"
    except Exception:
        for p in pon_ports:
            if p["status"] == "unknown":
                p["status"] = "active"

    # Remove campos internos antes de retornar
    for p in pon_ports:
        p.pop("_base", None)
        p.pop("if_index", None)

    # Ordena por slot, card, pon
    pon_ports.sort(key=lambda x: (x["slot"], x.get("card", 1), x["pon"]))

    logger.info(f"[SNMP] Descoberta concluída: {len(pon_ports)} portas PON")
    return pon_ports
