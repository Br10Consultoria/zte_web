"""
SNMP Client para ZTE Titan (C320/C600/C610/C620/C650)
Usa subprocess com snmpwalk/snmpget do sistema operacional.
Mais confiável que a API Python do pysnmp (que muda muito entre versões).

Instalação no servidor:
  apt-get install -y snmp

OIDs relevantes ZTE Titan:
  ifDescr         : 1.3.6.1.2.1.2.2.1.2      - Nome da interface (gpon-olt_1/1)
  ifOperStatus    : 1.3.6.1.2.1.2.2.1.8      - Status operacional (1=up, 2=down)
  sysDescr        : 1.3.6.1.2.1.1.1.0        - Modelo/firmware
  sysName         : 1.3.6.1.2.1.1.5.0        - Nome do sistema
"""
import re
import subprocess
import shutil
from typing import List, Dict, Optional, Tuple


# OIDs padrão MIB-II
OID_IF_DESCR        = "1.3.6.1.2.1.2.2.1.2"
OID_IF_OPER_STATUS  = "1.3.6.1.2.1.2.2.1.8"
OID_SYS_DESCR       = "1.3.6.1.2.1.1.1.0"
OID_SYS_NAME        = "1.3.6.1.2.1.1.5.0"


class SNMPError(Exception):
    pass


def _find_snmp_tool(name: str) -> Optional[str]:
    """Localiza o binário snmpwalk/snmpget no sistema."""
    path = shutil.which(name)
    if path:
        return path
    # Caminhos alternativos comuns
    for candidate in [f"/usr/bin/{name}", f"/usr/local/bin/{name}", f"/opt/homebrew/bin/{name}"]:
        import os
        if os.path.isfile(candidate):
            return candidate
    return None


def _snmp_walk(host: str, community: str, oid: str, port: int = 161,
               version: str = "2c", timeout: int = 10) -> List[Tuple[str, str]]:
    """
    Executa snmpwalk via subprocess.
    Retorna lista de (oid_str, value_str).
    """
    tool = _find_snmp_tool("snmpwalk")
    if not tool:
        raise SNMPError(
            "snmpwalk não encontrado. Instale com: apt-get install -y snmp"
        )

    cmd = [
        tool,
        "-v", version,
        "-c", community,
        "-t", str(timeout),
        "-r", "2",
        "-On",          # Exibe OIDs em formato numérico
        f"{host}:{port}",
        oid
    ]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout + 5
        )
    except subprocess.TimeoutExpired:
        raise SNMPError(f"Timeout ao executar snmpwalk em {host}:{port}")
    except FileNotFoundError:
        raise SNMPError("snmpwalk não encontrado. Instale com: apt-get install -y snmp")

    if result.returncode != 0 and not result.stdout:
        err = result.stderr.strip()
        raise SNMPError(f"snmpwalk falhou em {host}:{port}: {err}")

    rows = []
    for line in result.stdout.strip().split('\n'):
        line = line.strip()
        if not line:
            continue
        # Formato: .1.3.6.1.2.1.2.2.1.2.X = STRING: "gpon-olt_1/1"
        # ou:      .1.3.6.1.2.1.2.2.1.2.X = INTEGER: 1
        m = re.match(r'^([\d\.]+)\s*=\s*(?:\w+:\s*)?"?([^"]*)"?$', line)
        if m:
            rows.append((m.group(1).strip('.'), m.group(2).strip()))
        else:
            # Tenta formato mais simples
            parts = line.split('=', 1)
            if len(parts) == 2:
                oid_part = parts[0].strip().strip('.')
                val_part = parts[1].strip()
                # Remove tipo (STRING:, INTEGER:, etc.)
                val_part = re.sub(r'^\w+:\s*', '', val_part).strip('"').strip()
                rows.append((oid_part, val_part))

    return rows


def _snmp_get(host: str, community: str, oid: str, port: int = 161,
              version: str = "2c", timeout: int = 5) -> Optional[str]:
    """
    Executa snmpget via subprocess.
    Retorna o valor como string ou None.
    """
    tool = _find_snmp_tool("snmpget")
    if not tool:
        raise SNMPError("snmpget não encontrado. Instale com: apt-get install -y snmp")

    cmd = [
        tool,
        "-v", version,
        "-c", community,
        "-t", str(timeout),
        "-r", "1",
        "-On",
        f"{host}:{port}",
        oid
    ]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout + 3
        )
    except subprocess.TimeoutExpired:
        raise SNMPError(f"Timeout ao executar snmpget em {host}:{port}")
    except FileNotFoundError:
        raise SNMPError("snmpget não encontrado. Instale com: apt-get install -y snmp")

    if result.returncode != 0:
        raise SNMPError(f"snmpget falhou: {result.stderr.strip()}")

    # Parse da linha de resultado
    line = result.stdout.strip()
    m = re.search(r'=\s*(?:\w+:\s*)?"?([^"]+)"?$', line)
    if m:
        return m.group(1).strip()
    return line


def snmp_test_connection(host: str, community: str = "public", port: int = 161,
                         version: str = "2c") -> Tuple[bool, str]:
    """Testa conectividade SNMP com a OLT."""
    try:
        val = _snmp_get(host, community, OID_SYS_DESCR, port, version, timeout=5)
        if val:
            return True, val
        return False, "Sem resposta SNMP"
    except SNMPError as e:
        return False, str(e)
    except Exception as e:
        return False, f"Erro SNMP: {str(e)}"


def snmp_get_system_info(host: str, community: str = "public", port: int = 161,
                         version: str = "2c") -> Dict:
    """Obtém informações básicas do sistema via SNMP."""
    result = {}
    try:
        sys_descr = _snmp_get(host, community, OID_SYS_DESCR, port, version)
        if sys_descr:
            result["sys_descr"] = sys_descr
            # Extrai modelo: C320, C600, C610, C620, C650
            m = re.search(r'(C\d{3,4})', sys_descr, re.IGNORECASE)
            if m:
                result["model"] = f"ZTE {m.group(1).upper()}"
            else:
                result["model"] = "ZTE Titan"
            # Extrai versão de firmware
            fw = re.search(r'[Vv](\d+\.\d+[\.\d]*)', sys_descr)
            if fw:
                result["firmware"] = fw.group(1)

        sys_name = _snmp_get(host, community, OID_SYS_NAME, port, version)
        if sys_name:
            result["sys_name"] = sys_name

    except Exception as e:
        result["error"] = str(e)

    return result


def snmp_discover_pon_ports(host: str, community: str = "public", port: int = 161,
                            version: str = "2c") -> List[Dict]:
    """
    Descobre todas as portas PON via SNMP usando ifDescr.
    Filtra interfaces do tipo gpon-olt_SLOT/PON.
    Retorna lista de dicts com slot, pon, status, if_index.
    """
    # Verifica se snmpwalk está disponível
    if not _find_snmp_tool("snmpwalk"):
        raise SNMPError(
            "snmpwalk não encontrado no servidor. "
            "Instale com: apt-get install -y snmp"
        )

    # Walk em ifDescr para obter todos os nomes de interfaces
    try:
        if_descr_list = _snmp_walk(host, community, OID_IF_DESCR, port, version)
    except SNMPError:
        raise
    except Exception as e:
        raise SNMPError(f"Falha no SNMP walk ifDescr em {host}:{port} — {e}")

    if not if_descr_list:
        raise SNMPError(
            f"Nenhuma interface retornada via SNMP de {host}:{port}. "
            f"Verifique a community string e se o SNMP está habilitado na OLT."
        )

    # Filtra apenas interfaces gpon-olt_SLOT/PON
    pon_ports = []
    if_index_map = {}  # if_index -> (slot, pon)

    for oid_str, if_name in if_descr_list:
        if_name = if_name.strip()
        # Aceita: gpon-olt_1/1, gpon-olt_1/2, GPON-OLT_1/1, etc.
        m = re.match(r'gpon[-_]olt[_\-]?(\d+)/(\d+)$', if_name, re.IGNORECASE)
        if m:
            slot = int(m.group(1))
            pon = int(m.group(2))
            # Extrai o ifIndex do OID (último número)
            try:
                if_index = int(oid_str.split('.')[-1])
            except (ValueError, IndexError):
                if_index = 0
            if_index_map[if_index] = (slot, pon)
            pon_ports.append({
                "slot": slot,
                "pon": pon,
                "if_index": if_index,
                "if_name": if_name,
                "port_type": "gpon",
                "description": if_name,
                "status": "unknown",
            })

    if not pon_ports:
        # Debug: mostra as primeiras interfaces encontradas para diagnóstico
        sample = [v for _, v in if_descr_list[:10]]
        raise SNMPError(
            f"Nenhuma interface gpon-olt encontrada via SNMP em {host}. "
            f"Total de interfaces: {len(if_descr_list)}. "
            f"Exemplos: {sample}"
        )

    # Busca o status operacional (ifOperStatus) para cada porta
    try:
        if_oper_list = _snmp_walk(host, community, OID_IF_OPER_STATUS, port, version)
        oper_map = {}
        for oid_str, val in if_oper_list:
            try:
                idx = int(oid_str.split('.')[-1])
                oper_map[idx] = int(val) if val.isdigit() else 0
            except (ValueError, IndexError):
                pass

        for p in pon_ports:
            oper = oper_map.get(p["if_index"], 0)
            if oper == 1:
                p["status"] = "online"
            elif oper == 2:
                p["status"] = "offline"
            else:
                p["status"] = "unknown"
    except Exception:
        pass  # Status fica como "unknown" se não conseguir

    # Ordena por slot, pon
    pon_ports.sort(key=lambda x: (x["slot"], x["pon"]))
    return pon_ports
