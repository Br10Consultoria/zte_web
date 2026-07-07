"""
OLT Driver — Abstração multi-modelo para ZTE Titan.

Cada modelo de OLT tem comandos e formatos de interface diferentes.
Este módulo fornece uma camada de abstração que encapsula essas diferenças.

Modelos suportados:
  zte_c600  — ZTE C600 (formato: gpon_olt-SLOT/CARD/PON)
  zte_c300  — ZTE C300 (formato: gpon-olt_SLOT/CARD/PON)

Como adicionar um novo modelo:
  1. Crie uma subclasse de OLTDriver
  2. Implemente os métodos abstratos
  3. Registre no dicionário DRIVERS
"""
import re
import logging
from typing import List, Dict, Optional, Any

logger = logging.getLogger("olt_driver")

# ============================================================
# CONSTANTES DE MODELO
# ============================================================

OLT_MODELS = {
    "zte_c600": {
        "label": "ZTE C600",
        "vendor": "ZTE",
        "series": "C600",
        # Interface: gpon_olt-SLOT/CARD/PON  |  gpon_onu-SLOT/CARD/PON:ID
    },
    "zte_c300": {
        "label": "ZTE C300",
        "vendor": "ZTE",
        "series": "C300",
        # Interface: gpon-olt_SLOT/CARD/PON  |  gpon-onu_SLOT/CARD/PON:ID
    },
}


# ============================================================
# CLASSE BASE
# ============================================================

class OLTDriver:
    """Interface base para drivers de OLT."""

    model_key: str = "base"

    # --- Geração de interfaces ---

    def olt_iface(self, slot: int, card: int, pon: int) -> str:
        raise NotImplementedError

    def onu_iface(self, idx_or_slot, card: int = None, pon: int = None, onu_id: int = None) -> str:
        """
        Aceita dois formatos:
          onu_iface("1/1/12:1")           -> converte string para interface
          onu_iface(slot, card, pon, id)  -> usa parâmetros separados
        """
        if isinstance(idx_or_slot, str):
            # Formato: "SLOT/CARD/PON:ID" ou "SLOT/PON:ID"
            parts = idx_or_slot.replace(':', '/').split('/')
            if len(parts) == 4:
                return self._onu_iface_parts(int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3]))
            elif len(parts) == 3:
                return self._onu_iface_parts(1, int(parts[0]), int(parts[1]), int(parts[2]))
            return idx_or_slot
        return self._onu_iface_parts(int(idx_or_slot), int(card), int(pon), int(onu_id))

    def _onu_iface_parts(self, slot: int, card: int, pon: int, onu_id: int) -> str:
        raise NotImplementedError

    # --- Comandos ---

    def cmd_onu_state(self, olt_iface: str) -> str:
        raise NotImplementedError

    def cmd_onu_baseinfo(self, olt_iface: str) -> str:
        raise NotImplementedError

    def cmd_onu_detail(self, onu_iface: str) -> str:
        raise NotImplementedError

    def cmd_olt_rx(self, olt_iface: str) -> str:
        raise NotImplementedError

    def cmd_onu_power(self, onu_iface: str) -> str:
        raise NotImplementedError

    def cmd_onu_reboot(self, onu_iface: str) -> List[str]:
        """
        Retorna lista de comandos para reiniciar a ONU.
        O reboot requer entrada no modo de gerenciamento e confirmação.
        """
        raise NotImplementedError

    def cmd_onu_traffic(self, onu_iface: str) -> str:
        """Retorna comando para consultar tráfego da ONU (Bps/pps)."""
        raise NotImplementedError

    def parse_onu_traffic(self, output: str) -> dict:
        """Parseia output de show interface gpon_onu-... ou gpon-onu_..."""
        raise NotImplementedError

    def cmd_discover_ports(self) -> List[str]:
        """Retorna lista de comandos para descoberta de portas."""
        raise NotImplementedError

    # --- Parsers ---

    def parse_onu_state(self, output: str) -> List[Dict]:
        raise NotImplementedError

    def parse_onu_baseinfo(self, output: str) -> List[Dict]:
        raise NotImplementedError

    def parse_olt_rx(self, output: str) -> Dict[str, float]:
        raise NotImplementedError

    def parse_onu_detail(self, output: str) -> Dict:
        raise NotImplementedError

    def parse_onu_power(self, output: str) -> Dict:
        raise NotImplementedError

    def parse_discover_ports(self, output: str) -> List[Dict]:
        raise NotImplementedError

    def parse_onu_state_for_discover(self, output: str, slot: int, card: int, pon: int) -> bool:
        """Verifica se o output de show onu state indica porta válida."""
        return (
            output.strip() != "" and
            "invalid" not in output.lower() and
            "error" not in output.lower() and
            "not exist" not in output.lower() and
            "no such" not in output.lower() and
            "%" not in output
        )


# ============================================================
# PARSER DE TRÁFEGO (COMUM A TODOS OS MODELOS)
# ============================================================

def _parse_onu_traffic_common(output: str) -> dict:
    """
    Parseia output de 'show interface gpon_onu-S/C/P:ID' ou 'gpon-onu_...'.

    Exemplo de output ZTE C300/C610:
      ONU statistic:
         Input rate :             800704 Bps             1205 pps
         Output rate:            1629378 Bps             1565 pps
         Input bandwidth utilization :0.6%
         Output bandwidth utilization: N/A
      Interface peak rate:
         Input peak rate :             875532 Bps             1205 pps
         Output peak rate:            1703780 Bps             1715 pps
      Total statistic:
       Input :
          Bytes:288115074            Packets:576376
       Output:
          Bytes:995006374            Packets:932691
    """
    result = {
        "rx_bps":        None,
        "rx_pps":        None,
        "tx_bps":        None,
        "tx_pps":        None,
        "rx_bw_util":    None,
        "tx_bw_util":    None,
        "rx_peak_bps":   None,
        "tx_peak_bps":   None,
        "rx_total_bytes": None,
        "tx_total_bytes": None,
        "rx_total_pkts":  None,
        "tx_total_pkts":  None,
    }

    def _int(s):
        try:
            return int(s.replace(',', '').strip())
        except Exception:
            return None

    def _float(s):
        try:
            return float(s.replace('%', '').strip())
        except Exception:
            return None

    for line in output.split('\n'):
        line = line.strip()
        # Input rate :   800704 Bps   1205 pps
        m = re.match(r'Input rate\s*:\s*(\d+)\s*Bps\s*(\d+)\s*pps', line, re.IGNORECASE)
        if m:
            result['rx_bps'] = _int(m.group(1))
            result['rx_pps'] = _int(m.group(2))
            continue
        # Output rate:  1629378 Bps   1565 pps
        m = re.match(r'Output rate\s*:\s*(\d+)\s*Bps\s*(\d+)\s*pps', line, re.IGNORECASE)
        if m:
            result['tx_bps'] = _int(m.group(1))
            result['tx_pps'] = _int(m.group(2))
            continue
        # Input bandwidth utilization :0.6%
        m = re.match(r'Input bandwidth utilization\s*:\s*([\d\.]+)%', line, re.IGNORECASE)
        if m:
            result['rx_bw_util'] = _float(m.group(1))
            continue
        # Output bandwidth utilization: N/A  ou  1.2%
        m = re.match(r'Output bandwidth utilization\s*:\s*([\d\.]+)%', line, re.IGNORECASE)
        if m:
            result['tx_bw_util'] = _float(m.group(1))
            continue
        # Input peak rate :  875532 Bps  1205 pps
        m = re.match(r'Input peak rate\s*:\s*(\d+)\s*Bps', line, re.IGNORECASE)
        if m:
            result['rx_peak_bps'] = _int(m.group(1))
            continue
        # Output peak rate: 1703780 Bps  1715 pps
        m = re.match(r'Output peak rate\s*:\s*(\d+)\s*Bps', line, re.IGNORECASE)
        if m:
            result['tx_peak_bps'] = _int(m.group(1))
            continue
        # Bytes:288115074   Packets:576376  (dentro de Input)
        m = re.match(r'Bytes:(\d+)\s+Packets:(\d+)', line, re.IGNORECASE)
        if m:
            # Detecta se é Input ou Output pelo contexto anterior
            if result['rx_total_bytes'] is None:
                result['rx_total_bytes'] = _int(m.group(1))
                result['rx_total_pkts']  = _int(m.group(2))
            else:
                result['tx_total_bytes'] = _int(m.group(1))
                result['tx_total_pkts']  = _int(m.group(2))
            continue

    logger.debug(f"[PARSER] parse_onu_traffic: {result}")
    return result


# ============================================================
# DRIVER ZTE C600
# ============================================================

class ZTEC600Driver(OLTDriver):
    """
    Driver para ZTE C600.
    Formato de interface: gpon_olt-SLOT/CARD/PON  |  gpon_onu-SLOT/CARD/PON:ID
    """
    model_key = "zte_c600"

    def olt_iface(self, slot: int, card: int, pon: int) -> str:
        return f"gpon_olt-{slot}/{card}/{pon}"

    def _onu_iface_parts(self, slot: int, card: int, pon: int, onu_id: int) -> str:
        return f"gpon_onu-{slot}/{card}/{pon}:{onu_id}"

    def cmd_onu_state(self, olt_iface: str) -> str:
        return f"show gpon onu state {olt_iface}"

    def cmd_onu_baseinfo(self, olt_iface: str) -> str:
        return f"show gpon onu baseinfo {olt_iface}"

    def cmd_onu_detail(self, onu_iface: str) -> str:
        return f"show gpon onu detail-info {onu_iface}"

    def cmd_olt_rx(self, olt_iface: str) -> str:
        return f"show pon power olt-rx {olt_iface}"

    def cmd_onu_power(self, onu_iface: str) -> str:
        return f"show pon power attenuation {onu_iface}"

    def cmd_onu_reboot(self, onu_iface: str) -> List[str]:
        """
        Sequência de comandos para reboot da ONU no C600.
        Formato: gpon_onu-SLOT/CARD/PON:ID
        """
        return [
            f"pon-onu-mng {onu_iface}",
            "reboot",
            "y",
        ]

    def cmd_onu_traffic(self, onu_iface: str) -> str:
        return f"show interface {onu_iface}"

    def parse_onu_traffic(self, output: str) -> dict:
        return _parse_onu_traffic_common(output)

    def cmd_discover_ports(self) -> List[str]:
        return [
            "show gpon onu state",
        ]

    def parse_onu_state(self, output: str) -> List[Dict]:
        """
        Parseia: show gpon onu state gpon_olt-SLOT/CARD/PON
        Formato: 1/1/1:1   enable   enable   working   1(GPON)
        """
        onus = []
        seen = set()
        for line in output.split('\n'):
            line = line.strip()
            m = re.match(
                r'^(\d+/\d+(?:/\d+)?:\d+)\s+(\w+)\s+(\w+)\s+(\w+)',
                line
            )
            if not m:
                continue
            idx = m.group(1)
            if idx in seen:
                continue
            seen.add(idx)
            oper = m.group(4).lower()
            color = (
                "green"  if oper == "working"   else
                "red"    if oper in ("dyinggasp", "los", "losi", "lof", "poweroff") else
                "yellow" if oper in ("reboot", "omci-down", "deactive") else
                "gray"
            )
            onus.append({
                "onu_index":       idx,
                "admin_state":     m.group(2).lower(),
                "omcc_state":      m.group(3).lower(),
                "oper_state":      m.group(4),
                "last_down_cause": None,
                "status_color":    color,
            })
        logger.debug(f"[PARSER] parse_onu_state (C600): {len(onus)} ONUs")
        return onus

    def parse_onu_baseinfo(self, output: str) -> List[Dict]:
        """
        Parseia: show gpon onu baseinfo gpon_olt-SLOT/CARD/PON
        Formato: gpon_onu-1/3/16:1    ZTE-F601V6.    sn      SN:MONU007F8491         ready
        """
        onus = []
        seen = set()
        for line in output.split('\n'):
            line = line.strip()
            # Formato com prefixo gpon_onu- ou gpon-onu_
            m = re.match(
                r'^gpon[_-]onu[_-](\d+/\d+(?:/\d+)?:\d+)\s+(\S+)\s+\S+\s+(\S+)',
                line
            )
            if m:
                idx    = m.group(1)
                model  = m.group(2)
                serial = m.group(3).replace("SN:", "").strip()
                if idx not in seen:
                    seen.add(idx)
                    onus.append({"onu_index": idx, "model": model, "serial": serial})
                continue
            # Formato sem prefixo (fallback)
            m2 = re.match(
                r'^(\d+/\d+(?:/\d+)?:\d+)\s+(\S+)\s+(\S+)\s+(\S+)',
                line
            )
            if m2:
                idx    = m2.group(1)
                serial = m2.group(2)
                model  = m2.group(3)
                if idx not in seen:
                    seen.add(idx)
                    onus.append({"onu_index": idx, "model": model, "serial": serial})
        logger.debug(f"[PARSER] parse_onu_baseinfo (C600): {len(onus)} ONUs")
        return onus

    def parse_olt_rx(self, output: str) -> Dict[str, float]:
        """
        Parseia: show pon power olt-rx gpon_olt-SLOT/CARD/PON
        Formato: gpon_onu-1/3/16:1    -28.860(dbm)
        """
        rx_map = {}
        for line in output.split('\n'):
            line = line.strip()
            m = re.match(r'gpon[_-]onu[_-](\d+/\d+(?:/\d+)?:\d+)\s+([-\d\.]+)', line)
            if m:
                idx = m.group(1)
                try:
                    rx_map[idx] = float(m.group(2))
                except ValueError:
                    pass
        logger.debug(f"[PARSER] parse_olt_rx (C600): {len(rx_map)} ONUs")
        return rx_map

    def parse_onu_detail(self, output: str) -> Dict:
        """
        Parseia: show gpon onu detail-info gpon_onu-SLOT/CARD/PON:ID
        Extrai campos principais do detail-info.
        """
        result = {}
        fields = {
            "name":            r'Name\s*:\s*(.+)',
            "type":            r'Type\s*:\s*(\S+)',
            "state":           r'State\s*:\s*(\S+)',
            "admin_state":     r'Admin state\s*:\s*(\S+)',
            "phase_state":     r'Phase state\s*:\s*(\S+)',
            "config_state":    r'Config state\s*:\s*(\S+)',
            "serial":          r'Serial number\s*:\s*(\S+)',
            "description":     r'Description\s*:\s*(\S[^\n]*)',
            "distance":        r'ONU Distance\s*:\s*(\S+)',
            "online_duration": r'Online Duration\s*:\s*(\S[^\n]*)',
            "fec":             r'FEC\s*:\s*(\S+)',
        }
        for key, pattern in fields.items():
            m = re.search(pattern, output, re.IGNORECASE)
            if m:
                result[key] = m.group(1).strip()

        # Histórico de eventos (AuthpassTime / OfflineTime / Cause)
        history = []
        for m in re.finditer(
            r'(\d+)\s+([\d\-]+ [\d:]+)\s+([\d\-]+ [\d:]+)\s*(\S*)',
            output
        ):
            auth_t  = m.group(2).strip()
            off_t   = m.group(3).strip()
            cause   = m.group(4).strip()
            if auth_t and auth_t != "0000-00-00 00:00:00":
                history.append({
                    "authpass_time":  auth_t,
                    "offline_time":   off_t if off_t != "0000-00-00 00:00:00" else None,
                    "cause":          cause or None,
                })
        result["history"] = history
        return result

    def parse_onu_power(self, output: str) -> Dict:
        """
        Parseia: show pon power attenuation gpon_onu-SLOT/CARD/PON:ID
        Formato:
          up      Rx :-26.968(dbm)      Tx:2.463(dbm)        29.431(dB)
          down    Tx :6.623(dbm)        Rx:-22.678(dbm)      29.301(dB)
        """
        result = {}
        # up: OLT recebe (Rx) e ONU transmite (Tx)
        m_up = re.search(
            r'up\s+Rx\s*:\s*([-\d\.]+)\s*\(dbm\)\s+Tx\s*:\s*([-\d\.]+)\s*\(dbm\)\s+([\d\.]+)',
            output, re.IGNORECASE
        )
        if m_up:
            result["olt_rx_power"]    = float(m_up.group(1))
            result["onu_tx_power"]    = float(m_up.group(2))
            result["attenuation_up"]  = float(m_up.group(3))

        # down: OLT transmite (Tx) e ONU recebe (Rx)
        m_dn = re.search(
            r'down\s+Tx\s*:\s*([-\d\.]+)\s*\(dbm\)\s+Rx\s*:\s*([-\d\.]+)\s*\(dbm\)\s+([\d\.]+)',
            output, re.IGNORECASE
        )
        if m_dn:
            result["olt_tx_power"]      = float(m_dn.group(1))
            result["rx_power"]          = float(m_dn.group(2))
            result["attenuation_down"]  = float(m_dn.group(3))

        # Campos unificados para o frontend
        result["tx_power"]    = result.get("onu_tx_power")
        result["attenuation"] = result.get("attenuation_up") or result.get("attenuation_down")

        # Status de sinal
        rx = result.get("rx_power")
        if rx is not None:
            if rx >= -25:
                result["rx_status"] = "normal"
            elif rx >= -28:
                result["rx_status"] = "warning"
            else:
                result["rx_status"] = "critical"

        return result

    def parse_discover_ports(self, output: str) -> List[Dict]:
        """
        Parseia output de show gpon onu state.
        Formato: gpon_olt-SLOT/CARD/PON ou indices SLOT/CARD/PON:ONU
        """
        ports = []
        seen = set()
        for line in output.split('\n'):
            m_onu = re.match(r'^(\d+)/(\d+)/(\d+):\d+', line)
            if m_onu:
                slot = int(m_onu.group(1))
                card = int(m_onu.group(2))
                pon = int(m_onu.group(3))
                key = (slot, card, pon)
                if key not in seen:
                    seen.add(key)
                    ports.append({"slot": slot, "card": card, "pon": pon, "port_type": "gpon"})
                continue
            m3 = re.search(r'gpon[_-]olt[_-](\d+)/(\d+)/(\d+)', line)
            if m3:
                slot = int(m3.group(1))
                card = int(m3.group(2))
                pon  = int(m3.group(3))
                key  = (slot, card, pon)
                if key not in seen:
                    seen.add(key)
                    ports.append({"slot": slot, "card": card, "pon": pon, "port_type": "gpon"})
                continue
        return ports


# ============================================================
# DRIVER ZTE C300 / C300M / C300T (Titan)
# ============================================================

class ZTEC300Driver(OLTDriver):
    """
    Driver para ZTE C300/C300M/C300T.
    Formato de interface: gpon-olt_SLOT/CARD/PON  |  gpon-onu_SLOT/CARD/PON:ID
    Mesmo formato do C320 (hífen antes do underline).
    Confirmado na OLT C300 ARAMARI: 'show gpon onu state gpon-olt_1/2/1' funciona.
    """
    model_key = "zte_c300"

    def olt_iface(self, slot: int, card: int, pon: int) -> str:
        return f"gpon-olt_{slot}/{card}/{pon}"

    def _onu_iface_parts(self, slot: int, card: int, pon: int, onu_id: int) -> str:
        return f"gpon-onu_{slot}/{card}/{pon}:{onu_id}"

    def cmd_onu_state(self, olt_iface: str) -> str:
        # C300 aceita sem interface (lista todas) ou com interface específica
        return f"show gpon onu state {olt_iface}"

    def cmd_onu_baseinfo(self, olt_iface: str) -> str:
        return f"show gpon onu baseinfo {olt_iface}"

    def cmd_onu_detail(self, onu_iface: str) -> str:
        return f"show gpon onu detail-info {onu_iface}"

    def cmd_olt_rx(self, olt_iface: str) -> str:
        return f"show pon power olt-rx {olt_iface}"

    def cmd_onu_power(self, onu_iface: str) -> str:
        return f"show pon power attenuation {onu_iface}"

    def cmd_onu_reboot(self, onu_iface: str) -> List[str]:
        """
        Sequência de comandos para reboot da ONU no C300/C610/Titan.
        Formato: gpon_onu-SLOT/CARD/PON:ID
        """
        return [
            f"pon-onu-mng {onu_iface}",
            "reboot",
            "y",
        ]

    def cmd_onu_traffic(self, onu_iface: str) -> str:
        return f"show interface {onu_iface}"

    def parse_onu_traffic(self, output: str) -> dict:
        return _parse_onu_traffic_common(output)

    def cmd_discover_ports(self) -> List[str]:
        # Na C300/C610, 'show interface gpon_olt' sem iface específica retorna erro.
        # Usamos 'show gpon onu state' sem argumento para listar todas as ONUs
        # e depois derivamos as portas a partir dos índices retornados.
        return [
            "show gpon onu state",
        ]

    def parse_onu_state(self, output: str) -> List[Dict]:
        """
        Parseia: show gpon onu state [gpon_olt-SLOT/CARD/PON]
        Formato C300: 1/1/1:1   enable   enable   working   GPON
        (igual ao C320 mas com coluna Speed mode em vez de Channel)
        """
        onus = []
        seen = set()
        for line in output.split('\n'):
            line = line.strip()
            m = re.match(
                r'^(\d+/\d+(?:/\d+)?:\d+)\s+(\w+)\s+(\w+)\s+(\w+)',
                line
            )
            if not m:
                continue
            idx = m.group(1)
            if idx in seen:
                continue
            seen.add(idx)
            oper = m.group(4).lower()
            color = (
                "green"  if oper == "working"   else
                "red"    if oper in ("dyinggasp", "los", "losi", "lof", "poweroff") else
                "yellow" if oper in ("reboot", "omci-down", "deactive") else
                "gray"
            )
            onus.append({
                "onu_index":       idx,
                "admin_state":     m.group(2).lower(),
                "omcc_state":      m.group(3).lower(),
                "oper_state":      m.group(4),
                "last_down_cause": None,
                "status_color":    color,
            })
        logger.debug(f"[PARSER] parse_onu_state (C300): {len(onus)} ONUs")
        return onus

    def parse_onu_baseinfo(self, output: str) -> List[Dict]:
        """
        Parseia: show gpon onu baseinfo gpon-olt_SLOT/CARD/PON
        Formato C300: gpon-onu_1/2/1:1    ZTE-F600    sn      SN:DACMED71A961    ready
        """
        onus = []
        seen = set()
        for line in output.split('\n'):
            line = line.strip()
            # Formato com prefixo gpon-onu_ (C300/C320) ou gpon_onu- (legado)
            m = re.match(
                r'^gpon[_-]onu[_-](\d+/\d+(?:/\d+)?:\d+)\s+(\S+)\s+\S+\s+(\S+)',
                line
            )
            if m:
                idx    = m.group(1)
                model  = m.group(2)
                serial = m.group(3).replace("SN:", "").strip()
                if idx not in seen:
                    seen.add(idx)
                    onus.append({"onu_index": idx, "model": model, "serial": serial})
                continue
            # Fallback: sem prefixo
            m2 = re.match(
                r'^(\d+/\d+(?:/\d+)?:\d+)\s+(\S+)\s+(\S+)\s+(\S+)',
                line
            )
            if m2:
                idx    = m2.group(1)
                serial = m2.group(2)
                model  = m2.group(3)
                if idx not in seen:
                    seen.add(idx)
                    onus.append({"onu_index": idx, "model": model, "serial": serial})
        logger.debug(f"[PARSER] parse_onu_baseinfo (C300): {len(onus)} ONUs")
        return onus

    def parse_olt_rx(self, output: str) -> Dict[str, float]:
        """
        Parseia: show pon power olt-rx gpon-olt_SLOT/CARD/PON
        Formato C300: gpon-onu_1/2/1:1    -27.786(dbm)
        """
        rx_map = {}
        for line in output.split('\n'):
            line = line.strip()
            # Aceita ambos os formatos: gpon-onu_ e gpon_onu- (legado)
            m = re.match(r'gpon[_-]onu[_-](\d+/\d+(?:/\d+)?:\d+)\s+([-\d\.]+)', line)
            if m:
                idx = m.group(1)
                try:
                    rx_map[idx] = float(m.group(2))
                except ValueError:
                    pass
        logger.debug(f"[PARSER] parse_olt_rx (C300): {len(rx_map)} ONUs")
        return rx_map

    def parse_onu_detail(self, output: str) -> Dict:
        """
        Parseia: show gpon onu detail-info gpon_onu-SLOT/CARD/PON:ID
        Formato idêntico ao C320 nos campos principais.
        """
        # Reutiliza parser de campos comuns.
        return ZTEC600Driver().parse_onu_detail(output)

    def parse_onu_power(self, output: str) -> Dict:
        """
        Parseia: show pon power attenuation gpon_onu-SLOT/CARD/PON:ID
        Formato idêntico ao C320.
        """
        return ZTEC600Driver().parse_onu_power(output)

    def parse_discover_ports(self, output: str) -> List[Dict]:
        """
        Parseia output de 'show gpon onu state' (sem argumento) para descobrir portas.
        O C300/C610 não suporta 'show interface gpon_olt' sem especificar a interface.
        Extrai as portas únicas a partir dos índices de ONU (SLOT/CARD/PON:ID).

        Também aceita output de 'show interface gpon-olt_X/X/X' com múltiplas interfaces.
        """
        ports = []
        seen = set()

        for line in output.split('\n'):
            line = line.strip()

            # Extrai porta de índice de ONU: SLOT/CARD/PON:ID
            m_onu = re.match(r'^(\d+)/(\d+)/(\d+):\d+', line)
            if m_onu:
                slot = int(m_onu.group(1))
                card = int(m_onu.group(2))
                pon  = int(m_onu.group(3))
                key  = (slot, card, pon)
                if key not in seen:
                    seen.add(key)
                    ports.append({"slot": slot, "card": card, "pon": pon, "port_type": "gpon"})
                continue

            # Formato explícito: gpon-olt_SLOT/CARD/PON ou gpon_olt-SLOT/CARD/PON (legado)
            m3 = re.search(r'gpon[_-]olt[_-](\d+)/(\d+)/(\d+)', line)
            if m3:
                slot = int(m3.group(1))
                card = int(m3.group(2))
                pon  = int(m3.group(3))
                key  = (slot, card, pon)
                if key not in seen:
                    seen.add(key)
                    ports.append({"slot": slot, "card": card, "pon": pon, "port_type": "gpon"})

        logger.debug(f"[PARSER] parse_discover_ports (C300): {len(ports)} portas")
        return ports

    def parse_onu_state_for_discover(self, output: str, slot: int, card: int, pon: int) -> bool:
        """
        Na C300/C610, 'show gpon onu state gpon_olt-S/C/P' retorna:
          - Cabeçalho + linhas de ONUs: porta válida com ONUs
          - Apenas cabeçalho (sem ONUs): porta válida mas vazia
          - Mensagem de erro/invalid: porta não existe

        Aceita a porta somente se:
          1. Não contiver nenhum indicador de erro
          2. Tiver linhas de ONU OU cabeçalho de tabela
        """
        out = output.strip()
        if not out:
            return False
        out_lower = out.lower()
        # Rejeita se contiver qualquer indicador de erro
        # Inclui '%' sozinho para capturar '%Error', '% Error', etc.
        error_indicators = (
            "invalid input",
            "invalid command",
            "invalid parameter",
            "error",
            "not exist",
            "no such",
            "^",
        )
        if any(ind in out_lower for ind in error_indicators):
            return False
        if "%" in out:
            return False
        # Aceita SOMENTE se tiver cabeçalho de tabela OU linhas de ONU
        # Isso evita aceitar outputs ambíguos (ex: prompt vazio, mensagens genéricas)
        iface_prefix = f"{slot}/{card}/{pon}:"
        has_onus = any(line.strip().startswith(iface_prefix) for line in out.split('\n'))
        has_header = "onuindex" in out_lower or "admin state" in out_lower
        return has_onus or has_header


# ============================================================
# REGISTRO DE DRIVERS
# ============================================================

DRIVERS: Dict[str, OLTDriver] = {
    "zte_c600": ZTEC600Driver(),
    "zte_c300": ZTEC300Driver(),
    # Compatibilidade com registros antigos criados antes da separacao C600/C300.
    "zte_c320": ZTEC600Driver(),
}

# Driver padrão (retrocompatibilidade)
DEFAULT_DRIVER = DRIVERS["zte_c600"]


def get_driver(model_key: Optional[str]) -> OLTDriver:
    """Retorna o driver correto para o modelo da OLT."""
    if not model_key:
        return DEFAULT_DRIVER
    return DRIVERS.get(model_key, DEFAULT_DRIVER)


def detect_model(login_banner: str) -> str:
    """
    Tenta detectar o modelo da OLT pelo banner de login.
    Retorna a chave do modelo (ex: 'zte_c300') ou 'zte_c600' como padrão.

    Regra principal:
      - Banner contém "C600" ou "TITAN series" → zte_c600
      - Banner contém "C300" → zte_c300
    """
    banner_lower = login_banner.lower()

    if "c300" in banner_lower:
        return "zte_c300"

    if "c600" in banner_lower or "titan series" in banner_lower:
        return "zte_c600"

    # Detecta pelo formato da interface presente no output
    if "gpon_olt-" in login_banner or "gpon_onu-" in login_banner:
        return "zte_c600"
    if "gpon-olt_" in login_banner or "gpon-onu_" in login_banner:
        return "zte_c300"

    return "zte_c600"
