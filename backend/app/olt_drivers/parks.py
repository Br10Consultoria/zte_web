"""Driver Parks 3000/4000."""
import logging
import re
from typing import Dict, List, Optional

from .base import OLTDriver

logger = logging.getLogger("olt_driver")

# DRIVER PARKS 3000/4000
# ============================================================

class Parks30004000Driver(OLTDriver):
    """
    Driver para Parks 3000/4000.
    Formato da OLT: gponSLOT/PON. Internamente mantemos SLOT/1/PON:ONU
    para reaproveitar telas, cache e filtros existentes.
    """
    model_key = "parks_3000_4000"

    def login_pre_prompt_markers(self) -> List[str]:
        return ["press <return>", "press return"]

    def auth_failure_markers(self) -> List[str]:
        return super().auth_failure_markers() + ["%auth-4-login"]

    def pagination_disable_commands(self) -> List[str]:
        return ["screen-length 0 temporary"]

    def olt_iface(self, slot: int, card: int, pon: int) -> str:
        return f"gpon{slot}/{pon}"

    def _onu_iface_parts(self, slot: int, card: int, pon: int, onu_id: int) -> str:
        return f"gpon{slot}/{pon} onu {onu_id}"

    def _iface_from_onu_ref(self, onu_iface: str) -> str:
        m = re.search(r'(gpon\d+/\d+)\s+onu\s+\d+', onu_iface, re.IGNORECASE)
        return m.group(1) if m else onu_iface

    def _onu_id_from_ref(self, onu_iface: str) -> Optional[int]:
        m = re.search(r'\bonu\s+(\d+)\b', onu_iface, re.IGNORECASE)
        return int(m.group(1)) if m else None

    def cmd_onu_state(self, olt_iface: str) -> str:
        return f"show interface {olt_iface} onu status"

    def cmd_onu_baseinfo(self, olt_iface: str) -> str:
        return f"show interface {olt_iface} onu model"

    def cmd_onu_detail(self, onu_iface: str) -> str:
        return f"show interface {onu_iface} information"

    def cmd_olt_rx(self, olt_iface: str) -> str:
        return f"show interface {olt_iface} onu status"

    def cmd_onu_power(self, onu_iface: str) -> str:
        return f"show interface {onu_iface} status"

    def cmd_onu_service(self, onu_iface: str) -> str:
        return f"show interface {onu_iface}"

    def cmd_onu_equip(self, onu_iface: str) -> str:
        return f"show interface {onu_iface} information"

    def cmd_optical_module(self, olt_iface: str) -> str:
        return f"show interface {olt_iface} sfp"

    def cmd_onu_reboot(self, onu_iface: str) -> List[str]:
        iface = self._iface_from_onu_ref(onu_iface)
        onu_id = self._onu_id_from_ref(onu_iface)
        return ["configure terminal", f"interface {iface}", f"onu {onu_id} reset", "end"]

    def cmd_onu_traffic(self, onu_iface: str) -> str:
        return f"show interface {onu_iface}"

    def cmd_discover_ports(self) -> List[str]:
        return ["show interface gpon"]

    def cmd_backup_to_ftp(
        self,
        server_ip: str,
        filename: str,
        ftp_user: str,
        ftp_password: str,
        source_path: str,
    ) -> str:
        return f"copy running-config ftp://{server_ip}/{filename} {ftp_user}"

    def cmd_uncfg_onus(self) -> str:
        return "show gpon onu unconfigured"

    def _idx_from_iface(self, iface: str, onu_id: int) -> str:
        m = re.search(r'gpon(\d+)/(\d+)', iface or "", re.IGNORECASE)
        if not m:
            return f"1/1/1:{onu_id}"
        return f"{int(m.group(1))}/1/{int(m.group(2))}:{onu_id}"

    def _power_value(self, value: str) -> Optional[float]:
        if not value or "no signal" in value.lower():
            return None
        m = re.search(r'(-?\d+(?:\.\d+)?)\s*dB', value, re.IGNORECASE)
        if not m:
            return None
        try:
            return float(m.group(1))
        except ValueError:
            return None

    def _rx_status(self, value: Optional[float]) -> Optional[str]:
        if value is None:
            return None
        if value >= -27:
            return "normal"
        if value > -29:
            return "warning"
        return "critical"

    def parse_onu_state(self, output: str) -> List[Dict]:
        onus = []
        iface_match = re.search(r'Interface\s+(gpon\d+/\d+)\s*:', output, re.IGNORECASE)
        iface = iface_match.group(1) if iface_match else ""
        block_re = re.compile(
            r'^\s*(\d+)-([A-Za-z0-9]+):\s*$(.*?)(?=^\s*\d+-[A-Za-z0-9]+:\s*$|\Z)',
            re.IGNORECASE | re.MULTILINE | re.DOTALL,
        )
        for match in block_re.finditer(output):
            onu_id = int(match.group(1))
            serial = match.group(2).strip().upper()
            body = match.group(3)
            status_m = re.search(r'Status\s*:\s*(.+)', body, re.IGNORECASE)
            power_m = re.search(r'Power Level\s*:\s*(.+)', body, re.IGNORECASE)
            rssi_m = re.search(r'RSSI\s*:\s*(.+)', body, re.IGNORECASE)
            status = (status_m.group(1).strip() if status_m else "UNKNOWN")
            power = self._power_value(power_m.group(1).strip() if power_m else "")
            rssi = self._power_value(rssi_m.group(1).strip() if rssi_m else "")
            status_lower = status.lower()
            if "invalid" in status_lower:
                oper = "invalid"
            elif "inactive" in status_lower:
                oper = "inactive"
            elif "active" in status_lower:
                oper = "working"
            else:
                oper = "offline"
            no_signal = (
                (power_m and "no signal" in power_m.group(1).lower()) or
                (rssi_m and "no signal" in rssi_m.group(1).lower())
            )
            last_down = "NO SIGNAL" if no_signal else (status if oper != "working" else None)
            onus.append({
                "onu_index": self._idx_from_iface(iface, onu_id),
                "serial": serial,
                "admin_state": "enable" if oper == "working" else "disable",
                "omcc_state": status,
                "oper_state": oper,
                "last_down_cause": last_down,
                "status_color": "green" if oper == "working" else "red",
                "rx_power": power,
                "onu_rx_power": power,
                "rx_status": self._rx_status(power),
                "olt_rx_power": rssi,
                "olt_rx_status": self._rx_status(rssi),
            })
        logger.debug(f"[PARSER] parse_onu_state (Parks): {len(onus)} ONUs")
        return onus

    def parse_onu_baseinfo(self, output: str) -> List[Dict]:
        onus = []
        for line in output.splitlines():
            line = line.strip()
            if not line or line.lower().startswith("serial") or line.startswith("-"):
                continue
            if "|" in line:
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 2 and re.search(r'[A-Za-z]{4}[A-Fa-f0-9]+', parts[0]):
                    onus.append({"serial": parts[0].upper(), "model": parts[1]})
                continue
            m = re.match(r'([A-Za-z]{4}[A-Fa-f0-9]+)\s+(.+)$', line)
            if m:
                onus.append({"serial": m.group(1).upper(), "model": m.group(2).strip()})
        logger.debug(f"[PARSER] parse_onu_baseinfo (Parks): {len(onus)} ONUs")
        return onus

    def parse_olt_rx(self, output: str) -> Dict[str, float]:
        result = {}
        for onu in self.parse_onu_state(output):
            value = onu.get("olt_rx_power")
            if value is not None:
                result[onu["onu_index"]] = value
        return result

    def parse_onu_detail(self, output: str) -> Dict:
        result = {}
        iface_m = re.search(r'ONU Status for ONU\s+(\d+)', output, re.IGNORECASE)
        if iface_m:
            result["onu_number"] = iface_m.group(1)
        fields = {
            "state": r'ONU primary status\s*:\s*(.+)',
            "phase_state": r'ONU Protection Mode\s*:\s*(.+)',
            "secondary_status": r'ONU secondary status\s*:\s*(.+)',
            "vendor_id": r'Serial Number \(vendor ID\)\s*:\s*(.+)',
            "vendor_specific": r'Serial Number \(vendor Specific\)\s*:\s*(.+)',
        }
        for key, pattern in fields.items():
            m = re.search(pattern, output, re.IGNORECASE)
            if m:
                result[key] = " ".join(m.group(1).strip().split())
        if result.get("vendor_id") or result.get("vendor_specific"):
            vendor = (result.get("vendor_id") or "").replace(" ", "")
            specific = (result.get("vendor_specific") or "").replace(" ", "")
            result["serial"] = f"{vendor}{specific}".upper()
            result["vendor"] = vendor
        if result.get("state"):
            result["admin_state"] = "enable" if "active" in result["state"].lower() else "disable"
        result.setdefault("history", [])
        return result

    def parse_onu_power(self, output: str) -> Dict:
        states = self.parse_onu_state(output)
        if states:
            onu = states[0]
            return {
                "rx_power": onu.get("rx_power"),
                "onu_rx_power": onu.get("rx_power"),
                "olt_rx_power": onu.get("olt_rx_power"),
                "rx_status": onu.get("rx_status"),
                "olt_rx_status": onu.get("olt_rx_status"),
            }
        return {}

    def parse_optical_module_info(self, output: str) -> Dict:
        result = {}
        patterns = {
            "vendor": r"Name\s*:\s*(\S+)",
            "product": r"PN\s*:\s*(\S+)",
            "sequence_number": r"SN\s*:\s*(\S+)",
            "version_level": r"Rev\s*:\s*(\S+)",
            "connector": r"Connector\s*:\s*(\S+)",
            "wavelength": r"Laser Wavelength\s*:\s*(\d+)\s*nm",
            "temperature": r"Temperature\s*:\s*([-\d.]+)\s*C",
            "supply_voltage": r"Supply Voltage\s*:\s*([-\d.]+)\s*V",
            "tx_power": r"TX Output Power\s*:\s*([-\d.]+)\s*dBm",
            "rx_power": r"RX Input Power\s*:\s*([-\d.]+)\s*dBm",
        }
        numeric_fields = {"temperature", "supply_voltage", "tx_power", "rx_power"}
        for key, pattern in patterns.items():
            match = re.search(pattern, output, re.IGNORECASE)
            if not match:
                continue
            value = match.group(1).strip()
            if key in numeric_fields:
                try:
                    value = float(value)
                except ValueError:
                    pass
            result[key] = value
        return result

    def parse_onu_traffic(self, output: str) -> dict:
        return {}

    def parse_discover_ports(self, output: str) -> List[Dict]:
        ports = []
        seen = set()
        for m in re.finditer(r'\bgpon(\d+)/(\d+)\b', output, re.IGNORECASE):
            slot = int(m.group(1))
            pon = int(m.group(2))
            key = (slot, 1, pon)
            if key not in seen:
                seen.add(key)
                ports.append({"slot": slot, "card": 1, "pon": pon, "port_type": "gpon"})
        return ports

    def parse_onu_state_for_discover(self, output: str, slot: int, card: int, pon: int) -> bool:
        out = output.strip()
        if not out:
            return False
        lower = out.lower()
        if any(token in lower for token in ("invalid", "unknown", "not exist", "no such", "error")) or "%" in out:
            return False
        return f"interface gpon{slot}/{pon}" in lower or bool(re.search(r'^\s*\d+-[A-Za-z0-9]+:', out, re.MULTILINE))

    def parse_uncfg_onus(self, output: str) -> List[Dict]:
        onus = []
        for line in output.splitlines():
            line = line.strip()
            if not line or line.startswith("-") or line.lower().startswith("interface"):
                continue
            if "|" in line:
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 3 and parts[0].lower().startswith("gpon"):
                    iface = parts[0]
                    serial = parts[1].upper()
                    model = parts[2]
                    m = re.search(r'gpon(\d+)/(\d+)', iface, re.IGNORECASE)
                    idx = f"{int(m.group(1))}/1/{int(m.group(2))}" if m else iface
                    onus.append({
                        "onu_index": idx,
                        "olt_index": iface,
                        "model": model,
                        "serial": serial,
                        "password": "",
                    })
                continue
            m = re.match(r'(gpon\d+/\d+)\s+(\S+)\s+(.+)$', line, re.IGNORECASE)
            if m:
                iface = m.group(1)
                mi = re.search(r'gpon(\d+)/(\d+)', iface, re.IGNORECASE)
                idx = f"{int(mi.group(1))}/1/{int(mi.group(2))}" if mi else iface
                onus.append({
                    "onu_index": idx,
                    "olt_index": iface,
                    "model": m.group(3).strip(),
                    "serial": m.group(2).upper(),
                    "password": "",
                })
        return onus


# ============================================================
