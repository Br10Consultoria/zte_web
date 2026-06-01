"""
SNMP Client para ZTE Titan — descoberta de portas PON via snmpwalk/snmpget do sistema.

Estratégia de descoberta de portas (validada com ZTE C610 real):

  Passo 1 — ifName (OID padrão IF-MIB):
    OID: 1.3.6.1.2.1.31.1.1.1.1
    Retorna nomes como "gpon_olt-1/1/1", "gpon_olt-1/2/1" etc.
    Filtramos apenas entradas que começam com "gpon_olt-" ou "gpon-olt_".
    Essa OID funciona em TODOS os modelos ZTE (C320, C600, C610, C620, C650).

  Passo 2 — ONUs online por porta:
    OID: 1.3.6.1.4.1.3902.1082.500.10.2.2.3.1.15.<ifIndex>
    Retorna INTEGER com contagem de ONUs online.

  Passo 3 — ONUs autorizadas por porta:
    OID: 1.3.6.1.4.1.3902.1082.500.10.2.2.3.1.14.<ifIndex>
    Retorna INTEGER com total de ONUs provisionadas.

  Passo 4 — Status operacional:
    OID: 1.3.6.1.2.1.2.2.1.8.<ifIndex>  (ifOperStatus)
    1=up, 2=down

OIDs ZTE proprietárias (família 3902.1012) — usadas como fallback:
  1.3.6.1.4.1.3902.1012.3.13.1.1.1   — nome da porta PON (pode não funcionar em C610)
  1.3.6.1.4.1.3902.1012.3.13.1.1.13  — contagem de ONUs (pode não funcionar em C610)

ifIndex real do ZTE C610 (confirmado via snmpwalk):
  gpon_olt-1/1/1  → 285278465  (0x11010101)
  gpon_olt-1/1/2  → 285278466  (0x11010102)
  gpon_olt-1/2/1  → 285278721  (0x11010201)
  gpon_olt-1/3/1  → 285278977  (0x11010301)
"""
import subprocess
import re
import logging
from typing import List, Dict, Tuple, Optional

logger = logging.getLogger("snmp_client")

# OIDs padrão IF-MIB
OID_IF_NAME        = "1.3.6.1.2.1.31.1.1.1.1"   # ifName — nome da interface
OID_IF_OPER_STATUS = "1.3.6.1.2.1.2.2.1.8"       # ifOperStatus
OID_SYS_DESCR      = "1.3.6.1.2.1.1.1.0"
OID_SYS_NAME       = "1.3.6.1.2.1.1.5.0"

# OIDs ZTE proprietárias (família 3902.1082) — validadas com C610 real
OID_ZTE_ONU_ONLINE    = "1.3.6.1.4.1.3902.1082.500.10.2.2.3.1.15"  # ONUs online por porta
OID_ZTE_ONU_AUTH      = "1.3.6.1.4.1.3902.1082.500.10.2.2.3.1.14"  # ONUs autorizadas por porta

# OIDs ZTE proprietárias (família 3902.1012) — fallback para C320/C600 legado
OID_ZTE_PON_NAME      = "1.3.6.1.4.1.3902.1012.3.13.1.1.1"
OID_ZTE_PON_ONU_COUNT = "1.3.6.1.4.1.3902.1012.3.13.1.1.13"


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


def _snmp_walk(host: str, community: str, oid: str, port: int = 161,
               version: str = "2c", timeout: int = 20) -> List[Tuple[int, str]]:
    """
    Executa snmpwalk via subprocess.
    Retorna lista de (last_oid_component_int, value_str).
    Usa -Cc para ignorar erros "OID not increasing" (comum em ZTE C610).
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
        "-Cc",          # ignora "OID not increasing" (ZTE C610 retorna fora de ordem)
        f"{host}:{port}",
        oid
    ]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout + 15
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
        for prefix in ["STRING:", "INTEGER:", "Gauge32:", "Counter32:", "OID:",
                        "IpAddress:", "Timeticks:", "Hex-STRING:"]:
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


def _parse_gpon_ifname(if_name: str) -> Optional[Tuple[int, int, int]]:
    """
    Extrai (slot, card, pon) de um nome de interface GPON.

    Formatos suportados:
      gpon_olt-1/1/1   → slot=1, card=1, pon=1  (ZTE C300/C610 Titan)
      gpon-olt_1/1/1   → slot=1, card=1, pon=1  (ZTE C320/C600/C620)

    Retorna None se não for interface GPON PON.
    """
    # Formato C300/C610: gpon_olt-SLOT/CARD/PON
    m = re.match(r'^gpon_olt-(\d+)/(\d+)/(\d+)$', if_name.strip())
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))

    # Formato C320/C600: gpon-olt_SLOT/CARD/PON
    m = re.match(r'^gpon-olt_(\d+)/(\d+)/(\d+)$', if_name.strip())
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))

    return None


def snmp_discover_pon_ports(host: str, community: str, port: int = 161,
                            version: str = "2c",
                            ssh_port: int = None, ssh_username: str = None,
                            ssh_password: str = None, ssh_protocol: str = None,
                            olt_model: str = None) -> List[Dict]:
    """
    Descobre todas as portas PON via SNMP.

    Estratégia principal (funciona em C610, C320, C600, C620):
      1. Walk em ifName (1.3.6.1.2.1.31.1.1.1.1) — retorna nomes reais das interfaces
      2. Filtra apenas interfaces GPON (gpon_olt- ou gpon-olt_)
      3. Busca contagem de ONUs online via OID ZTE 3902.1082
      4. Busca status operacional via ifOperStatus

    Fallback (para C320 legado que não suporta ifName):
      Walk em OID proprietária ZTE 3902.1012.3.13.1.1.1
    """
    if not _find_snmp_tool("snmpwalk"):
        raise SNMPError(
            "snmpwalk não encontrado. Instale com: apt-get install -y snmp"
        )

    # -------------------------------------------------------
    # Passo 1: Descoberta via ifName (OID padrão IF-MIB)
    # -------------------------------------------------------
    logger.info(f"[SNMP] Descobrindo portas via ifName em {host}:{port}")
    pon_ports = []
    if_index_map = {}  # if_name → if_index

    try:
        if_rows = _snmp_walk(host, community, OID_IF_NAME, port, version)
        logger.info(f"[SNMP] ifName retornou {len(if_rows)} interfaces")

        for if_index, if_name in if_rows:
            parsed = _parse_gpon_ifname(if_name)
            if parsed is None:
                continue  # não é interface GPON PON

            slot, card, pon = parsed
            if_index_map[if_index] = if_name

            pon_ports.append({
                "slot":        slot,
                "card":        card,
                "pon":         pon,
                "if_index":    if_index,
                "if_name":     if_name,
                "port_type":   "gpon",
                "description": if_name,
                "status":      "unknown",
                "onu_count":   0,
            })

        logger.info(f"[SNMP] {len(pon_ports)} portas GPON encontradas via ifName")

    except SNMPError as e:
        logger.warning(f"[SNMP] ifName falhou: {e}")

    # -------------------------------------------------------
    # Fallback: OID proprietária ZTE (para C320 legado)
    # -------------------------------------------------------
    if not pon_ports:
        logger.info("[SNMP] Tentando OID proprietária ZTE (fallback para C320 legado)")
        try:
            pon_rows = _snmp_walk(host, community, OID_ZTE_PON_NAME, port, version)
            valid_rows = [(idx, val) for idx, val in pon_rows if idx > 1000]

            if valid_rows:
                all_indices = sorted([idx for idx, _ in valid_rows])
                # Detecta base: menor índice múltiplo de 256
                diffs = [all_indices[i+1] - all_indices[i]
                         for i in range(min(len(all_indices)-1, 5))]
                step_ok = all(d % 256 == 0 for d in diffs) if diffs else False
                base = all_indices[0] if step_ok else (all_indices[0] - (all_indices[0] % 65536))

                for if_index, val in valid_rows:
                    diff = if_index - base
                    slot = diff // 65536 + 1
                    pon_num = (diff % 65536) // 256 + 1
                    if not (1 <= slot <= 16 and 1 <= pon_num <= 16):
                        continue

                    if_name = f"gpon-olt_1/{slot}/{pon_num}"
                    if_index_map[if_index] = if_name
                    pon_ports.append({
                        "slot":        1,
                        "card":        slot,
                        "pon":         pon_num,
                        "if_index":    if_index,
                        "if_name":     if_name,
                        "port_type":   "gpon",
                        "description": val if val else f"OLT-{pon_num}",
                        "status":      "unknown",
                        "onu_count":   0,
                    })

                logger.info(f"[SNMP] Fallback ZTE: {len(pon_ports)} portas encontradas")

        except SNMPError as e:
            logger.warning(f"[SNMP] Fallback ZTE falhou: {e}")

    if not pon_ports:
        raise SNMPError(
            f"Nenhuma porta PON encontrada via SNMP em {host}:{port}. "
            f"Verifique a community string '{community}' e se o SNMP está habilitado na OLT."
        )

    # -------------------------------------------------------
    # Passo 2: Contagem de ONUs online (OID 3902.1082)
    # -------------------------------------------------------
    try:
        onu_online_rows = _snmp_walk(host, community, OID_ZTE_ONU_ONLINE, port, version)
        onu_online_map = {idx: val for idx, val in onu_online_rows}
        for p in pon_ports:
            val = onu_online_map.get(p["if_index"], "0")
            try:
                p["onu_count"] = int(val)
            except (ValueError, TypeError):
                p["onu_count"] = 0
        logger.info(f"[SNMP] Contagem de ONUs online obtida via OID 3902.1082")
    except Exception as e:
        logger.warning(f"[SNMP] Falha ao obter ONUs online (3902.1082): {e}")
        # Fallback: OID 3902.1012
        try:
            onu_rows = _snmp_walk(host, community, OID_ZTE_PON_ONU_COUNT, port, version)
            onu_map = {idx: val for idx, val in onu_rows if idx > 1000}
            for p in pon_ports:
                val = onu_map.get(p["if_index"], "0")
                try:
                    p["onu_count"] = int(val) if str(val).isdigit() else 0
                except (ValueError, TypeError):
                    p["onu_count"] = 0
        except Exception:
            pass

    # -------------------------------------------------------
    # Passo 3: Status operacional via ifOperStatus
    # -------------------------------------------------------
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

    # Remove campo interno if_index antes de retornar
    for p in pon_ports:
        p.pop("if_index", None)

    # Ordena por slot, card, pon
    pon_ports.sort(key=lambda x: (x["slot"], x.get("card", 1), x["pon"]))

    logger.info(f"[SNMP] Descoberta concluída: {len(pon_ports)} portas PON")
    return pon_ports
