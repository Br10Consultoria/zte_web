"""
SNMP Client para ZTE Titan (C600/C610/C620/C650)
Versão: SNMPv2c
Usado para descoberta rápida de interfaces PON e coleta de dados.

OIDs relevantes ZTE Titan:
  ifDescr         : 1.3.6.1.2.1.2.2.1.2      - Nome da interface (ex: gpon-olt_1/1)
  ifOperStatus    : 1.3.6.1.2.1.2.2.1.8      - Status operacional (1=up, 2=down)
  ifAdminStatus   : 1.3.6.1.2.1.2.2.1.7      - Status admin (1=up, 2=down)
  ifIndex         : 1.3.6.1.2.1.2.2.1.1      - Índice da interface
  sysDescr        : 1.3.6.1.2.1.1.1.0        - Descrição do sistema (modelo/firmware)
  sysName         : 1.3.6.1.2.1.1.5.0        - Nome do sistema
  sysUpTime       : 1.3.6.1.2.1.1.3.0        - Uptime

ZTE GPON OIDs (MIB proprietária):
  zxAnGponOltTable  : 1.3.6.1.4.1.3902.1082.500.10.2.1
  zxAnGponOnuTable  : 1.3.6.1.4.1.3902.1082.500.10.2.2
"""
import re
from typing import List, Dict, Optional, Tuple

try:
    from pysnmp.hlapi.v3arch.asyncio import *
    from pysnmp.hlapi import *
    PYSNMP_AVAILABLE = True
except ImportError:
    try:
        from pysnmp.hlapi import *
        PYSNMP_AVAILABLE = True
    except ImportError:
        PYSNMP_AVAILABLE = False


# OIDs padrão MIB-II
OID_IF_DESCR        = "1.3.6.1.2.1.2.2.1.2"
OID_IF_OPER_STATUS  = "1.3.6.1.2.1.2.2.1.8"
OID_IF_ADMIN_STATUS = "1.3.6.1.2.1.2.2.1.7"
OID_SYS_DESCR       = "1.3.6.1.2.1.1.1.0"
OID_SYS_NAME        = "1.3.6.1.2.1.1.5.0"
OID_SYS_UPTIME      = "1.3.6.1.2.1.1.3.0"


class SNMPError(Exception):
    pass


def _snmp_get(host: str, community: str, oid: str, port: int = 161,
              version: str = "2c", timeout: int = 5, retries: int = 2):
    """Executa um SNMP GET simples."""
    if not PYSNMP_AVAILABLE:
        raise SNMPError("pysnmp não está instalado")

    error_indication, error_status, error_index, var_binds = next(
        getCmd(
            SnmpEngine(),
            CommunityData(community, mpModel=1 if version == "2c" else 0),
            UdpTransportTarget((host, port), timeout=timeout, retries=retries),
            ContextData(),
            ObjectType(ObjectIdentity(oid))
        )
    )

    if error_indication:
        raise SNMPError(f"SNMP GET error: {error_indication}")
    if error_status:
        raise SNMPError(f"SNMP GET status error: {error_status.prettyPrint()}")

    return var_binds


def _snmp_walk(host: str, community: str, oid: str, port: int = 161,
               version: str = "2c", timeout: int = 10, retries: int = 2) -> List[Tuple]:
    """Executa um SNMP WALK (getBulk/getNext) e retorna lista de (oid, value)."""
    if not PYSNMP_AVAILABLE:
        raise SNMPError("pysnmp não está instalado")

    results = []
    for (error_indication, error_status, error_index, var_binds) in nextCmd(
        SnmpEngine(),
        CommunityData(community, mpModel=1 if version == "2c" else 0),
        UdpTransportTarget((host, port), timeout=timeout, retries=retries),
        ContextData(),
        ObjectType(ObjectIdentity(oid)),
        lexicographicMode=False
    ):
        if error_indication:
            break
        if error_status:
            break
        for var_bind in var_binds:
            results.append((str(var_bind[0]), str(var_bind[1])))

    return results


def snmp_get_system_info(host: str, community: str = "public", port: int = 161,
                         version: str = "2c") -> Dict:
    """Obtém informações básicas do sistema via SNMP."""
    result = {}
    try:
        for oid, label in [(OID_SYS_DESCR, "sys_descr"), (OID_SYS_NAME, "sys_name")]:
            try:
                var_binds = _snmp_get(host, community, oid, port, version)
                if var_binds:
                    result[label] = str(var_binds[0][1])
            except Exception:
                pass

        # Extrai modelo e firmware da sysDescr
        if "sys_descr" in result:
            desc = result["sys_descr"]
            model_match = re.search(r'(C600|C610|C620|C650|TITAN)', desc, re.IGNORECASE)
            fw_match = re.search(r'[Vv]ersion\s+([\d\.]+)', desc)
            if model_match:
                result["model"] = f"ZTE Titan {model_match.group(1).upper()}"
            if fw_match:
                result["firmware"] = fw_match.group(1)

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
    if not PYSNMP_AVAILABLE:
        raise SNMPError("pysnmp não disponível — instale com: pip install pysnmp")

    # Walk em ifDescr para obter todos os nomes de interfaces
    try:
        if_descr_list = _snmp_walk(host, community, OID_IF_DESCR, port, version)
    except Exception as e:
        raise SNMPError(f"Falha no SNMP walk ifDescr em {host}:{port} — {e}")

    if not if_descr_list:
        raise SNMPError(f"Nenhuma interface retornada via SNMP de {host}:{port}. Verifique community e acesso SNMP.")

    # Filtra apenas interfaces gpon-olt_SLOT/PON
    pon_ports = []
    if_index_map = {}  # if_index -> (slot, pon)

    for oid_str, if_name in if_descr_list:
        # Aceita: gpon-olt_1/1, gpon-olt_1/2, gpon_1/1, etc.
        m = re.match(r'gpon[-_]olt[_\-]?(\d+)/(\d+)$', if_name.strip(), re.IGNORECASE)
        if not m:
            # Tenta formato alternativo: gpon_1/1 (sem "olt")
            m = re.match(r'gpon[_\-](\d+)/(\d+)$', if_name.strip(), re.IGNORECASE)
        if m:
            slot = int(m.group(1))
            pon = int(m.group(2))
            # Extrai o ifIndex do OID (último número)
            if_index = int(oid_str.split('.')[-1])
            if_index_map[if_index] = (slot, pon)
            pon_ports.append({
                "slot": slot,
                "pon": pon,
                "if_index": if_index,
                "if_name": if_name.strip(),
                "port_type": "gpon",
                "description": if_name.strip(),
                "status": "unknown",
                "oper_status_raw": None,
            })

    if not pon_ports:
        raise SNMPError(
            f"Nenhuma interface gpon-olt encontrada via SNMP em {host}. "
            f"Total de interfaces retornadas: {len(if_descr_list)}. "
            f"Verifique se a OLT suporta SNMP e se a community '{community}' está correta."
        )

    # Busca o status operacional (ifOperStatus) para cada porta encontrada
    try:
        if_oper_list = _snmp_walk(host, community, OID_IF_OPER_STATUS, port, version)
        oper_map = {}
        for oid_str, val in if_oper_list:
            idx = int(oid_str.split('.')[-1])
            oper_map[idx] = int(val) if val.isdigit() else 0

        for p in pon_ports:
            oper = oper_map.get(p["if_index"], 0)
            p["oper_status_raw"] = oper
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


def snmp_test_connection(host: str, community: str = "public", port: int = 161,
                         version: str = "2c") -> Tuple[bool, str]:
    """Testa conectividade SNMP com a OLT."""
    try:
        var_binds = _snmp_get(host, community, OID_SYS_DESCR, port, version, timeout=5)
        if var_binds:
            sys_descr = str(var_binds[0][1])
            return True, sys_descr
        return False, "Sem resposta SNMP"
    except SNMPError as e:
        return False, str(e)
    except Exception as e:
        return False, f"Erro SNMP: {str(e)}"
