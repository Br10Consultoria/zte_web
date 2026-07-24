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
    "parks_3000_4000": {
        "label": "Parks 3000/4000",
        "vendor": "Parks",
        "series": "3000/4000",
        # Interface: gponSLOT/PON  |  ONU exibida internamente como SLOT/1/PON:ID
    },
}


# ============================================================
# CLASSE BASE
# ============================================================

class OLTDriver:
    """Interface base para drivers de OLT."""

    model_key: str = "base"

    # --- Perfil da sessao CLI ---

    def login_pre_prompt_markers(self) -> List[str]:
        """Marcadores que exigem ENTER antes do prompt de usuario."""
        return []

    def username_prompt_markers(self) -> List[str]:
        return ["username:"]

    def password_prompt_markers(self) -> List[str]:
        return ["password:"]

    def auth_failure_markers(self) -> List[str]:
        return ["login failed", "authentication failed", "access denied"]

    def pagination_disable_commands(self) -> List[str]:
        return []

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

    def cmd_onu_service(self, onu_iface: str) -> str:
        return f"show gpon remote-onu service {onu_iface}"

    def cmd_onu_equip(self, onu_iface: str) -> str:
        return f"show gpon remote-onu equip {onu_iface}"

    def cmd_optical_module(self, olt_iface: str) -> str:
        return f"show optical-module-info {olt_iface}"

    def cmd_onu_reboot(self, onu_iface: str) -> List[str]:
        """
        Retorna lista de comandos para reiniciar a ONU.
        O reboot requer entrada no modo de gerenciamento e confirmação.
        """
        raise NotImplementedError

    def cmd_onu_traffic(self, onu_iface: str) -> str:
        """Retorna comando para consultar tráfego da ONU (Bps/pps)."""
        raise NotImplementedError

    def cmd_backup_to_ftp(
        self,
        server_ip: str,
        filename: str,
        ftp_user: str,
        ftp_password: str,
        source_path: str,
    ) -> str:
        return f"copy ftp root: {source_path} //{server_ip}/{filename}@{ftp_user}:{ftp_password}"

    def cmd_uncfg_onus(self) -> str:
        return "show pon onu uncfg"

    def parse_uncfg_onus(self, output: str) -> List[Dict]:
        return []

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
