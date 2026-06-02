"""
OLT Client para ZTE Titan (C320/C600/C610/C620/C650)

Sintaxe de interface ZTE Titan:
  Porta PON:  gpon-olt_SLOT/CARD/PON   ex: gpon-olt_1/2/1
  ONU:        gpon-onu_SLOT/CARD/PON:ID ex: gpon-onu_1/2/2:85

Comandos utilizados (apenas show/consulta):
  show gpon onu state gpon-olt_1/2/2
  show gpon onu detail-info gpon-onu_1/2/2:85
  show pon power attenuation gpon-onu_1/2/2:85
  show gpon onu uncfg
  show gpon onu baseinfo gpon-olt_1/2/2
"""
import re
import time
import socket
import logging
import warnings
import paramiko
from typing import Optional, List, Dict, Any, Tuple
from .config import settings

# telnetlib está disponível no Python 3.11 (deprecado apenas no 3.13)
with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    try:
        import telnetlib as _telnetlib
        _TELNETLIB_OK = True
    except ImportError:
        _TELNETLIB_OK = False

# ============================================================
# LOGGER DETALHADO
# ============================================================
logger = logging.getLogger("olt_client")


def _log(level: str, msg: str):
    """Log com timestamp para acompanhamento em tempo real."""
    getattr(logger, level)(msg)


# ============================================================
# Wrapper Telnet: usa telnetlib nativo (Python 3.11) ou socket puro (3.13+)
# ============================================================
class SimpleTelnet:
    """
    Cliente Telnet que usa telnetlib nativo do Python quando disponível.
    O telnetlib lida automaticamente com todas as negociações IAC,
    o que é essencial para equipamentos como o ZTE C600/C610 que enviam
    múltiplas negociações logo após a conexão TCP.

    Fallback para socket puro quando telnetlib não está disponível (Python 3.13+).
    """

    # Constantes IAC (usadas apenas no fallback socket puro)
    IAC  = bytes([255])
    DONT = bytes([254])
    DO   = bytes([253])
    WONT = bytes([252])
    WILL = bytes([251])

    def __init__(self, host: str, port: int, timeout: int = 30):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._tn = None    # instância telnetlib.Telnet
        self.sock = None   # socket puro (fallback)
        self._buf = b""
        self._use_telnetlib = _TELNETLIB_OK

    def open(self):
        _log("info", f"[TELNET] Conectando a {self.host}:{self.port}")
        if self._use_telnetlib:
            # telnetlib lida automaticamente com negociações IAC
            self._tn = _telnetlib.Telnet()
            self._tn.open(self.host, self.port, timeout=self.timeout)
            _log("info", f"[TELNET] Conexão TCP estabelecida com {self.host}:{self.port} (via telnetlib)")
        else:
            self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
            self.sock.settimeout(self.timeout)
            _log("info", f"[TELNET] Conexão TCP estabelecida com {self.host}:{self.port} (via socket)")

    def _recv_raw(self, size: int = 4096) -> bytes:
        try:
            return self.sock.recv(size)
        except socket.timeout:
            return b""

    def _process_iac(self, data: bytes) -> bytes:
        """
        Processa negociações IAC (usado apenas no modo socket puro).
        Responde a todas as negociações DO/WILL/DONT/WONT.
        """
        out = b""
        i = 0
        while i < len(data):
            if data[i:i+1] == self.IAC:
                if i + 1 < len(data) and data[i+1:i+2] == self.IAC:
                    out += self.IAC
                    i += 2
                elif i + 2 < len(data):
                    cmd = data[i+1:i+2]
                    opt = data[i+2:i+3]
                    try:
                        if cmd == self.DO:
                            self.sock.sendall(self.IAC + self.WONT + opt)
                        elif cmd == self.WILL:
                            self.sock.sendall(self.IAC + self.DONT + opt)
                        elif cmd == self.DONT:
                            self.sock.sendall(self.IAC + self.WONT + opt)
                        elif cmd == self.WONT:
                            self.sock.sendall(self.IAC + self.DONT + opt)
                    except Exception:
                        pass
                    i += 3
                else:
                    i += 1
            else:
                out += data[i:i+1]
                i += 1
        return out

    def read_until(self, expected: bytes, timeout: int = 15) -> bytes:
        if self._use_telnetlib and self._tn:
            try:
                return self._tn.read_until(expected, timeout=timeout)
            except EOFError:
                return b""
        # Fallback socket puro
        deadline = time.time() + timeout
        while time.time() < deadline:
            raw = self._recv_raw()
            if raw:
                self._buf += self._process_iac(raw)
            if expected in self._buf:
                idx = self._buf.index(expected) + len(expected)
                result = self._buf[:idx]
                self._buf = self._buf[idx:]
                return result
            time.sleep(0.1)
        return self._buf

    def read_very_eager(self, wait: float = 0.5) -> bytes:
        time.sleep(wait)
        if self._use_telnetlib and self._tn:
            try:
                return self._tn.read_very_eager()
            except EOFError:
                return b""
        # Fallback socket puro
        self.sock.settimeout(0.3)
        try:
            data = b""
            while True:
                chunk = self._recv_raw(8192)
                if not chunk:
                    break
                data += self._process_iac(chunk)
            return data
        except Exception:
            return b""
        finally:
            self.sock.settimeout(self.timeout)

    def write(self, data: bytes):
        if self._use_telnetlib and self._tn:
            self._tn.write(data)
        else:
            self.sock.sendall(data)

    def close(self):
        try:
            if self._use_telnetlib and self._tn:
                self._tn.close()
            elif self.sock:
                self.sock.close()
        except Exception:
            pass


# ============================================================
# CLIENTE SSH
# ============================================================
class OLTSSHClient:
    """Cliente SSH para comunicação com OLTs ZTE Titan."""

    def __init__(self, ip: str, port: int, username: str, password: str):
        self.ip = ip
        self.port = port
        self.username = username
        self.password = password
        self.client = None
        self.shell = None

    def connect(self):
        _log("info", f"[SSH] Conectando a {self.ip}:{self.port} como '{self.username}'")
        try:
            self.client = paramiko.SSHClient()
            self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self.client.connect(
                self.ip, port=self.port,
                username=self.username, password=self.password,
                timeout=settings.SSH_TIMEOUT,
                look_for_keys=False, allow_agent=False
            )
            self.shell = self.client.invoke_shell(width=300, height=50)
            time.sleep(1.5)
            if self.shell.recv_ready():
                banner = self.shell.recv(8192).decode("utf-8", errors="replace")
                _log("debug", f"[SSH] Banner: {banner[:200]}")
            # Desabilita paginação
            self.shell.send("terminal length 0\n")
            time.sleep(0.8)
            if self.shell.recv_ready():
                self.shell.recv(8192)
            _log("info", f"[SSH] Conectado com sucesso a {self.ip}:{self.port}")
        except OLTConnectionError:
            raise
        except Exception as e:
            raise OLTConnectionError(f"Falha SSH em {self.ip}:{self.port} — {e}")

    def execute_command(self, command: str, timeout: int = None) -> str:
        if not self.shell:
            raise OLTConnectionError("Shell SSH não disponível")

        timeout = timeout or settings.SSH_COMMAND_TIMEOUT
        _log("info", f"[SSH] Executando: {command}")
        self.shell.send(command + "\n")
        time.sleep(0.5)

        output = ""
        self.shell.settimeout(timeout)
        start_time = time.time()

        try:
            while time.time() - start_time < timeout:
                if self.shell.recv_ready():
                    chunk = self.shell.recv(8192).decode("utf-8", errors="replace")
                    output += chunk
                    # Trata paginação --More--
                    if re.search(r'--\s*[Mm]ore\s*--', chunk):
                        self.shell.send(" ")
                        time.sleep(0.3)
                        continue
                    # Detecta prompt final (# ou >)
                    if re.search(r'[>#]\s*$', chunk.strip()):
                        break
                else:
                    time.sleep(0.1)
                    if not self.shell.recv_ready():
                        time.sleep(0.4)
                        if not self.shell.recv_ready():
                            break
        except socket.timeout:
            pass

        clean = _clean_output(output, command)
        _log("debug", f"[SSH] Resposta ({len(clean)} chars): {clean[:300]}")
        return clean

    def disconnect(self):
        try:
            if self.shell:
                self.shell.close()
            if self.client:
                self.client.close()
            _log("info", f"[SSH] Desconectado de {self.ip}")
        except Exception:
            pass


# ============================================================
# CLIENTE TELNET
# ============================================================
class OLTTelnetClient:
    """Cliente Telnet para comunicação com OLTs ZTE Titan."""

    def __init__(self, ip: str, port: int, username: str, password: str):
        self.ip = ip
        self.port = port
        self.username = username
        self.password = password
        self.tn = None
        self._prompt = b"#"

    def connect(self):
        _log("info", f"[TELNET] Conectando a {self.ip}:{self.port} como '{self.username}'")
        try:
            self.tn = SimpleTelnet(self.ip, self.port, timeout=settings.SSH_TIMEOUT)
            self.tn.open()

            # Aguarda prompt de usuário.
            # ZTE C600/C610 exibe aviso de segurança antes do prompt:
            #   "Warning: Telnet is not a secure protocol..."
            # Precisamos aguardar completamente antes de enviar o login.
            data = self.tn.read_until(b"Username:", timeout=25)
            decoded_pre = data.decode("utf-8", errors="replace")
            _log("debug", f"[TELNET] Recebido antes de Username: {decoded_pre[-200:]}")

            # Pequena pausa para garantir que todas as negociações IAC foram processadas
            time.sleep(0.3)
            self.tn.write(self.username.encode("ascii") + b"\n")

            # Aguarda prompt de senha
            data = self.tn.read_until(b"Password:", timeout=15)
            _log("debug", f"[TELNET] Recebido antes de Password: {data.decode('utf-8', errors='replace')[-100:]}")

            # Pausa antes de enviar senha (evita reset em equipamentos lentos)
            time.sleep(0.5)
            self.tn.write(self.password.encode("ascii") + b"\n")

            # Aguarda prompt da OLT (#).
            # ZTE C600/C610 exibe banner longo após login:
            #   "Welcome to TITAN series OLT of ZTE Corporation"
            # Aguardamos até 30s para o banner completo + prompt.
            data = self.tn.read_until(b"#", timeout=30)
            decoded = data.decode("utf-8", errors="replace")
            _log("debug", f"[TELNET] Após login: {decoded[-300:]}")

            if "#" not in decoded and ">" not in decoded:
                # Tenta mais uma vez (alguns equipamentos são lentos)
                extra = self.tn.read_very_eager(wait=2.0)
                decoded += extra.decode("utf-8", errors="replace")
                if "#" not in decoded and ">" not in decoded:
                    raise OLTConnectionError(
                        f"Login Telnet falhou — prompt não encontrado. "
                        f"Resposta: {decoded[-200:]}"
                    )

            # Detecta o prompt real (ex: "ENTER#", "GPON_JCSFIBRA#")
            m = re.search(r'([\w\-\.]+)[#>]\s*$', decoded.strip())
            if m:
                self._prompt = (m.group(1) + "#").encode("ascii")
                _log("info", f"[TELNET] Prompt detectado: {self._prompt.decode()}")

            # Desabilita paginação
            time.sleep(0.3)
            self.tn.write(b"terminal length 0\n")
            time.sleep(1.0)
            self.tn.read_very_eager(wait=0.8)
            _log("info", f"[TELNET] Conectado com sucesso a {self.ip}:{self.port}")

        except OLTConnectionError:
            raise
        except Exception as e:
            raise OLTConnectionError(f"Falha Telnet em {self.ip}:{self.port} — {e}")

    def execute_command(self, command: str, timeout: int = None) -> str:
        timeout = timeout or settings.SSH_COMMAND_TIMEOUT
        _log("info", f"[TELNET] Executando: {command}")
        self.tn.write(command.encode("ascii") + b"\n")

        output = ""
        start = time.time()
        last_data_time = time.time()

        while time.time() - start < timeout:
            chunk_bytes = self.tn.read_very_eager(wait=0.3)
            if chunk_bytes:
                chunk = chunk_bytes.decode("utf-8", errors="replace")
                output += chunk
                last_data_time = time.time()

                # Trata paginação --More--
                if re.search(r'--\s*[Mm]ore\s*--', chunk):
                    self.tn.write(b" ")
                    time.sleep(0.2)
                    continue

                # Detecta prompt final
                if re.search(r'[>#]\s*$', chunk.strip()):
                    break
            else:
                # Se ficou 1.5s sem dados e já temos saída, considera completo
                if output and (time.time() - last_data_time) > 1.5:
                    break
                time.sleep(0.1)

        clean = _clean_output(output, command)
        _log("debug", f"[TELNET] Resposta ({len(clean)} chars): {clean[:300]}")
        return clean

    def disconnect(self):
        try:
            if self.tn:
                self.tn.close()
            _log("info", f"[TELNET] Desconectado de {self.ip}")
        except Exception:
            pass


# ============================================================
# UTILITÁRIOS
# ============================================================

class OLTConnectionError(Exception):
    pass


def _clean_output(output: str, command: str) -> str:
    """Remove eco do comando, prompts e linhas vazias do output."""
    lines = output.split('\n')
    clean = []
    cmd_stripped = command.strip()
    for line in lines:
        stripped = line.strip()
        # Remove eco do comando
        if stripped == cmd_stripped:
            continue
        # Remove linhas de prompt puro (ex: "GPON_JCSFIBRA#")
        if re.match(r'^[\w\-\.]+[#>]\s*$', stripped):
            continue
        # Remove --More--
        if re.match(r'^--\s*[Mm]ore\s*--', stripped):
            continue
        clean.append(line.rstrip())
    return '\n'.join(clean).strip()


def get_olt_client(ip: str, port: int, username: str, password: str, protocol: str):
    """Factory para criar o cliente correto baseado no protocolo."""
    if protocol.lower() == "ssh":
        return OLTSSHClient(ip, port, username, password)
    elif protocol.lower() == "telnet":
        return OLTTelnetClient(ip, port, username, password)
    else:
        raise ValueError(f"Protocolo não suportado: {protocol}")


def _olt_iface(slot: int, card: int, pon: int) -> str:
    """
    Gera referência de porta PON no formato ZTE C320: gpon-olt_RACK/SLOT/PON
    RACK = 1 (fixo), SLOT = número da placa (card), PON = porta
    Exemplo: slot=1, card=1, pon=3 → gpon-olt_1/1/3
             slot=1, card=2, pon=5 → gpon-olt_1/2/5
    """
    return f"gpon-olt_1/{card}/{pon}"


def _onu_iface(slot: int, card: int, pon: int, onu_id: int) -> str:
    """
    Gera referência de ONU no formato ZTE C320: gpon-onu_RACK/SLOT/PON:ID
    RACK = 1 (fixo), SLOT = número da placa (card), PON = porta
    Exemplo: slot=1, card=2, pon=2, onu_id=85 → gpon-onu_1/2/2:85
    """
    return f"gpon-onu_1/{card}/{pon}:{onu_id}"


# ============================================================
# PARSERS — baseados na saída REAL da ZTE Titan C320
# ============================================================

def parse_onu_state(output: str) -> List[Dict]:
    """
    Parseia: show gpon onu state gpon-olt_1/2/2
    Formato real:
      OnuIndex   Admin State  OMCC State  Phase State  Channel
      1/2/2:1    enable       enable      working      1(GPON)
      1/2/2:22   enable       disable     DyingGasp    1(GPON)
    """
    onus = []
    lines = output.split('\n')
    for line in lines:
        line_stripped = line.strip()
        if not line_stripped:
            continue

        # Aceita SLOT/CARD/PON:ID ou SLOT/PON:ID
        m = re.match(
            r'^(\d+/\d+(?:/\d+)?:\d+)\s+'
            r'(enable|disable)\s+'
            r'(enable|disable)\s+'
            r'(\S+)\s*'
            r'(\S*)',
            line_stripped
        )
        if not m:
            continue

        onu_index  = m.group(1)
        admin_st   = m.group(2)
        omcc_st    = m.group(3)
        phase_st   = m.group(4)
        channel    = m.group(5) if m.group(5) else ""

        # Status de cor baseado no phase_state
        if phase_st.lower() == "working":
            color = "green"
            oper_state = "working"
        elif phase_st.lower() in ("initial", "ranging", "standby"):
            color = "yellow"
            oper_state = phase_st.lower()
        else:
            # DyingGasp, LOSi, offline, disable, etc.
            color = "red"
            oper_state = phase_st.lower()

        onus.append({
            "onu_index":       onu_index,
            "admin_state":     admin_st,
            "omcc_state":      omcc_st,
            "oper_state":      oper_state,
            "phase_state":     phase_st,
            "channel":         channel,
            "last_down_cause": phase_st if color == "red" else None,
            "status_color":    color,
        })

    _log("debug", f"[PARSER] parse_onu_state: {len(onus)} ONUs encontradas")
    return onus


def parse_onu_detail(output: str, onu_index: str) -> Dict:
    """
    Parseia: show gpon onu detail-info gpon-onu_1/2/2:85
    Extrai todos os campos relevantes da saída real da OLT.
    """
    result = {"onu_index": onu_index}

    # Mapeamento campo: regex
    fields = {
        "name":           r'Name\s*:\s*(.+)',
        "onu_type":       r'Type\s*:\s*(\S+)',
        "state":          r'State\s*:\s*(\S+)',
        "admin_state":    r'Admin state\s*:\s*(\S+)',
        "phase_state":    r'Phase state\s*:\s*(\S+)',
        "config_state":   r'Config state\s*:\s*(\S+)',
        "auth_mode":      r'Authentication mode\s*:\s*(\S+)',
        "serial_number":  r'Serial number\s*:\s*(\S+)',
        "description":    r'Description\s*:\s*(.+)',
        "vport_mode":     r'Vport mode\s*:\s*(\S+)',
        "dba_mode":       r'DBA Mode\s*:\s*(\S+)',
        "onu_status":     r'ONU Status\s*:\s*(\S+)',
        "line_profile":   r'Line Profile\s*:\s*(.+)',
        "service_profile":r'Service Profile\s*:\s*(.+)',
        "distance":       r'ONU Distance\s*:\s*(.+)',
        "online_duration":r'Online Duration\s*:\s*(.+)',
        "fec":            r'FEC\s*:\s*(\S+)',
    }

    for key, pattern in fields.items():
        m = re.search(pattern, output, re.IGNORECASE)
        if m:
            val = m.group(1).strip()
            if val and val.lower() not in ("n/a", "none", ""):
                result[key] = val

    # Histórico de quedas (tabela Authpass/Offline/Cause)
    history = []
    # Formato: "   1   2026-05-27 04:50:49    2026-05-28 11:17:37     LOSi"
    for m in re.finditer(
        r'^\s*(\d+)\s+'
        r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s+'
        r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s*'
        r'(\S*)',
        output, re.MULTILINE
    ):
        auth_time    = m.group(2).strip()
        offline_time = m.group(3).strip()
        cause        = m.group(4).strip() if m.group(4) else ""

        # Ignora entradas zeradas
        if auth_time == "0000-00-00 00:00:00":
            continue

        history.append({
            "seq":          int(m.group(1)),
            "auth_time":    auth_time,
            "offline_time": offline_time if offline_time != "0000-00-00 00:00:00" else None,
            "cause":        cause if cause else None,
        })

    if history:
        result["history"] = history

    _log("debug", f"[PARSER] parse_onu_detail {onu_index}: {list(result.keys())}")
    return result


def parse_onu_detail_batch(output: str) -> Dict[str, Dict]:
    """
    Parseia: show gpon onu detail-info gpon-olt_SLOT/CARD/PON
    Retorna dict indexado por onu_index (ex: '1/1/1:1') com campos:
      description, online_duration
    """
    results: Dict[str, Dict] = {}

    # Divide o output em blocos por ONU interface
    blocks = re.split(r'(?=ONU interface:\s+gpon-onu_)', output)

    for block in blocks:
        if not block.strip():
            continue
        m_iface = re.search(r'ONU interface:\s+gpon-onu_(\S+)', block)
        if not m_iface:
            continue
        onu_index = m_iface.group(1).strip()

        # Description: captura apenas o conteúdo na mesma linha (não vazio)
        description = ""
        m_desc = re.search(r'Description\s*:\s*(\S[^\n]*)', block)
        if m_desc:
            description = m_desc.group(1).strip()

        # Online Duration
        online_duration = ""
        m_up = re.search(r'Online Duration\s*:\s*(\S[^\n]*)', block)
        if m_up:
            online_duration = m_up.group(1).strip()

        results[onu_index] = {
            "description":    description,
            "online_duration": online_duration,
        }

    _log("debug", f"[PARSER] parse_onu_detail_batch: {len(results)} ONUs")
    return results


def parse_onu_power(output: str, onu_index: str) -> Dict:
    """
    Parseia: show pon power attenuation gpon-onu_1/2/2:85
    Saída real:
           OLT                  ONU              Attenuation
      up      Rx :-27.099(dbm)      Tx:2.442(dbm)        29.541(dB)
      down    Tx :5.025(dbm)        Rx:-27.695(dbm)      32.720(dB)
    """
    result = {"onu_index": onu_index}

    def _safe_float(val: str):
        """Converte string para float, retorna None se inválido."""
        try:
            f = float(val)
            import math
            if math.isnan(f) or math.isinf(f):
                return None
            return round(f, 3)
        except (ValueError, TypeError):
            return None

    # Upstream: OLT Rx (potência recebida pela OLT vinda da ONU)
    m = re.search(r'up\s+Rx\s*:\s*([-\d\.]+)\s*\(dbm\)', output, re.IGNORECASE)
    if m:
        rx_olt = _safe_float(m.group(1))
        if rx_olt is not None:
            result["olt_rx_power"] = rx_olt
            if rx_olt >= -27:
                result["olt_rx_status"] = "normal"
            elif rx_olt >= -29:
                result["olt_rx_status"] = "warning"
            else:
                result["olt_rx_status"] = "critical"

    # Upstream: ONU Tx
    m = re.search(r'up\s+Rx\s*:[-\d\.]+\s*\(dbm\)\s+Tx\s*:\s*([-\d\.]+)\s*\(dbm\)', output, re.IGNORECASE)
    if m:
        v = _safe_float(m.group(1))
        if v is not None:
            result["onu_tx_power"] = v

    # Upstream: Atenuação
    m = re.search(r'up\s+.*?(\d+\.\d+)\s*\(dB\)', output, re.IGNORECASE)
    if m:
        v = _safe_float(m.group(1))
        if v is not None:
            result["up_attenuation"] = v

    # Downstream: OLT Tx
    m = re.search(r'down\s+Tx\s*:\s*([-\d\.]+)\s*\(dbm\)', output, re.IGNORECASE)
    if m:
        v = _safe_float(m.group(1))
        if v is not None:
            result["olt_tx_power"] = v

    # Downstream: ONU Rx — mapeado como rx_power/rx_status para o frontend
    m = re.search(r'down\s+.*?Rx\s*:\s*([-\d\.]+)\s*\(dbm\)', output, re.IGNORECASE)
    if m:
        onu_rx = _safe_float(m.group(1))
        if onu_rx is not None:
            result["onu_rx_power"] = onu_rx
            result["rx_power"] = onu_rx  # alias para o frontend
            if onu_rx >= -27:
                result["onu_rx_status"] = "normal"
                result["rx_status"] = "normal"
            elif onu_rx >= -29:
                result["onu_rx_status"] = "warning"
                result["rx_status"] = "warning"
            else:
                result["onu_rx_status"] = "critical"
                result["rx_status"] = "critical"

    # Upstream: ONU Tx — alias tx_power para o frontend
    if "onu_tx_power" in result:
        result["tx_power"] = result["onu_tx_power"]

    # Downstream: Atenuação — alias attenuation para o frontend
    m = re.search(r'down\s+.*?(\d+\.\d+)\s*\(dB\)', output, re.IGNORECASE)
    if m:
        v = _safe_float(m.group(1))
        if v is not None:
            result["down_attenuation"] = v
            result["attenuation"] = v  # alias para o frontend

    _log("debug", f"[PARSER] parse_onu_power {onu_index}: {result}")
    return result


def parse_onu_baseinfo(output: str) -> List[Dict]:
    """
    Parseia: show gpon onu baseinfo gpon-olt_1/2/2
    Extrai serial, modelo, estado de cada ONU.

    Formato real ZTE C320 (com prefixo gpon-onu_):
      gpon-onu_1/1/1:1    ZTE-F660    sn      SN:TPLGBDC90DD8         ready
      gpon-onu_1/2/2:85   ZTE-F660    sn      SN:ZTEGC1234567         working

    Formato alternativo (sem prefixo):
      1/2/2:85  ITBS0DC456AC  ZTE-F660  enable  working
    """
    onus = []
    for line in output.split('\n'):
        line_stripped = line.strip()
        if not line_stripped:
            continue

        # Formato 1: com prefixo gpon-onu_
        # gpon-onu_1/1/1:1    ZTE-F660    sn      SN:TPLGBDC90DD8         ready
        m1 = re.match(
            r'^gpon-onu_(\d+/\d+(?:/\d+)?:\d+)\s+(\S+)\s+\S+\s+(\S+)\s*(\S*)',
            line_stripped
        )
        if m1:
            onu_index = m1.group(1)
            model     = m1.group(2)
            serial    = m1.group(3)
            state     = m1.group(4) if m1.group(4) else "unknown"
            # Remove prefixo SN: do serial se presente
            if serial.upper().startswith("SN:"):
                serial = serial[3:]
            onus.append({
                "onu_index":   onu_index,
                "serial":      serial,
                "model":       model,
                "admin_state": "enable",
                "oper_state":  state,
            })
            continue

        # Formato 2: sem prefixo — 1/2/2:85  ITBS0DC456AC  ZTE-F660  enable  working
        m2 = re.match(
            r'^(\d+/\d+(?:/\d+)?:\d+)\s+(\S+)\s+(\S+)\s+(enable|disable)\s*(\S*)',
            line_stripped
        )
        if m2 and ':' in m2.group(1):
            onus.append({
                "onu_index":   m2.group(1),
                "serial":      m2.group(2),
                "model":       m2.group(3),
                "admin_state": m2.group(4),
                "oper_state":  m2.group(5) if m2.group(5) else "unknown",
            })

    _log("debug", f"[PARSER] parse_onu_baseinfo: {len(onus)} ONUs")
    return onus


def parse_olt_rx_power(output: str) -> Dict[str, float]:
    """
    Parseia: show pon power olt-rx gpon-olt_SLOT/CARD/PON
    Retorna dict {onu_index: rx_olt_dbm} para todas as ONUs da porta.

    Formatos possíveis:
      OnuIndex     OLT-Rx-Power(dBm)
      1/1/1:1      -27.033
      gpon-onu_1/1/1:1   -27.033
    """
    result = {}

    def _safe_float(val: str):
        try:
            import math
            f = float(val)
            if math.isnan(f) or math.isinf(f):
                return None
            return round(f, 3)
        except (ValueError, TypeError):
            return None

    for line in output.split('\n'):
        line = line.strip()
        if not line:
            continue
        # Formato com prefixo: gpon-onu_1/1/1:1   -27.033
        m = re.match(r'gpon-onu_(\d+/\d+(?:/\d+)?:\d+)\s+([-\d\.]+)', line)
        if m:
            v = _safe_float(m.group(2))
            if v is not None:
                result[m.group(1)] = v
            continue
        # Formato sem prefixo: 1/1/1:1   -27.033
        m2 = re.match(r'^(\d+/\d+(?:/\d+)?:\d+)\s+([-\d\.]+)', line)
        if m2:
            v = _safe_float(m2.group(2))
            if v is not None:
                result[m2.group(1)] = v

    _log("debug", f"[PARSER] parse_olt_rx_power: {len(result)} entradas")
    return result


def parse_uncfg_onus(output: str) -> List[Dict]:
    """
    Parseia: show gpon onu uncfg
    Retorna ONUs não provisionadas com serial e interface.
    """
    onus = []
    for line in output.split('\n'):
        line = line.strip()
        # Formato: gpon-onu_1/2/2:99  ZTEG12345678
        m = re.match(r'gpon-onu_(\d+/\d+(?:/\d+)?:\d+)\s+(\S+)', line)
        if m:
            onus.append({"onu_index": m.group(1), "serial": m.group(2)})
            continue
        # Formato sem prefixo: 1/2/2:99  ZTEG12345678
        m2 = re.match(r'(\d+/\d+(?:/\d+)?:\d+)\s+(\S+)', line)
        if m2:
            onus.append({"onu_index": m2.group(1), "serial": m2.group(2)})
    _log("debug", f"[PARSER] parse_uncfg_onus: {len(onus)} ONUs não provisionadas")
    return onus


def parse_olt_ports(output: str) -> List[Dict]:
    """
    Parseia interfaces gpon-olt da OLT.
    Aceita formato 3 partes: gpon-olt_SLOT/CARD/PON (preferencial)
    Aceita formato 2 partes: gpon-olt_SLOT/PON (fallback)
    """
    ports = []
    seen = set()
    for line in output.split('\n'):
        # Formato 3 partes primeiro (mais específico)
        m3 = re.search(r'gpon-olt_(\d+)/(\d+)/(\d+)', line)
        if m3:
            slot = int(m3.group(1))
            card = int(m3.group(2))
            pon  = int(m3.group(3))
            key = (slot, card, pon)
            if key not in seen:
                seen.add(key)
                ports.append({"slot": slot, "card": card, "pon": pon, "port_type": "gpon"})
            continue
        # Formato 2 partes (card=1 por padrão)
        m2 = re.search(r'gpon-olt_(\d+)/(\d+)(?!\s*/)', line)
        if m2:
            slot = int(m2.group(1))
            pon  = int(m2.group(2))
            key = (slot, 1, pon)
            if key not in seen:
                seen.add(key)
                ports.append({"slot": slot, "card": 1, "pon": pon, "port_type": "gpon"})
    return ports


def parse_software_version(output: str) -> Dict:
    """Parseia versão do software da OLT."""
    result = {}
    m = re.search(r'[Vv]ersion\s*[:\-]\s*(\S+)', output)
    if m:
        result["firmware"] = m.group(1)
    m = re.search(r'(C\d{3,4})', output)
    if m:
        result["model"] = f"ZTE {m.group(1)}"
    return result


# ============================================================
# FUNÇÕES DE ALTO NÍVEL
# ============================================================

def test_olt_connection(ip: str, port: int, username: str, password: str,
                        protocol: str) -> Tuple[bool, str]:
    """Testa a conexão com uma OLT."""
    client = None
    try:
        _log("info", f"[TEST] Testando conexão {protocol.upper()} em {ip}:{port}")
        client = get_olt_client(ip, port, username, password, protocol)
        client.connect()
        output = client.execute_command("show software")
        _log("info", f"[TEST] Conexão OK em {ip}:{port}")
        return True, output
    except OLTConnectionError as e:
        _log("error", f"[TEST] Falha em {ip}:{port}: {e}")
        return False, str(e)
    except Exception as e:
        _log("error", f"[TEST] Erro inesperado em {ip}:{port}: {e}")
        return False, f"Erro inesperado: {str(e)}"
    finally:
        if client:
            try:
                client.disconnect()
            except Exception:
                pass


def discover_olt_ports(ip: str, port: int, username: str, password: str,
                       protocol: str, snmp_community: str = None,
                       snmp_port: int = 161, snmp_version: str = "2c") -> List[Dict]:
    """
    Descobre as portas PON disponíveis na OLT via SSH/Telnet.
    A descoberta SNMP é feita separadamente via snmp_client.
    Estratégia SSH/Telnet:
      1. show interface gpon-olt (lista todas de uma vez)
      2. Varredura: slots 1-4, cards 1-4, PON 1-16
    """
    client = None
    try:
        _log("info", f"[DISCOVER] Iniciando descoberta SSH/Telnet em {ip}:{port}")
        client = get_olt_client(ip, port, username, password, protocol)
        client.connect()

        ports = []
        seen = set()

        # Estratégia 1: listar todas as interfaces de uma vez
        for cmd in ["show interface gpon-olt", "show running-config interface gpon-olt"]:
            _log("info", f"[DISCOVER] Tentando: {cmd}")
            output = client.execute_command(cmd, timeout=20)
            found = parse_olt_ports(output)
            for p in found:
                key = (p["slot"], p["card"], p["pon"])
                if key not in seen:
                    seen.add(key)
                    ports.append(p)
            if ports:
                _log("info", f"[DISCOVER] Encontradas {len(ports)} portas via '{cmd}'")
                break

        # Estratégia 2: varredura slot/card/pon
        if not ports:
            _log("info", "[DISCOVER] Iniciando varredura slot/card/pon")
            for slot in range(1, 5):
                for card in range(1, 5):
                    for pon in range(1, 17):
                        key = (slot, card, pon)
                        if key in seen:
                            continue
                        iface = _olt_iface(slot, card, pon)
                        _log("debug", f"[DISCOVER] Testando {iface}")
                        try:
                            out = client.execute_command(
                                f"show gpon onu state {iface}", timeout=8
                            )
                            is_valid = (
                                out.strip() != "" and
                                "invalid" not in out.lower() and
                                "error" not in out.lower() and
                                "not exist" not in out.lower() and
                                "no such" not in out.lower() and
                                "%" not in out
                            )
                            if is_valid:
                                seen.add(key)
                                ports.append({
                                    "slot": slot, "card": card, "pon": pon,
                                    "port_type": "gpon",
                                    "description": iface
                                })
                                _log("info", f"[DISCOVER] Porta válida: {iface}")
                        except Exception as ex:
                            _log("debug", f"[DISCOVER] {iface} inválida: {ex}")

        ports.sort(key=lambda x: (x["slot"], x["card"], x["pon"]))
        _log("info", f"[DISCOVER] Total de portas descobertas: {len(ports)}")
        return ports

    except Exception as e:
        _log("error", f"[DISCOVER] Falha na descoberta: {e}")
        raise OLTConnectionError(f"Falha na descoberta: {str(e)}")
    finally:
        if client:
            try:
                client.disconnect()
            except Exception:
                pass


def get_pon_onu_status(ip: str, port: int, username: str, password: str,
                       protocol: str, slot: int, card: int, pon: int) -> List[Dict]:
    """
    Obtém o status de todas as ONUs de uma porta PON.
    Comando: show gpon onu state gpon-olt_SLOT/CARD/PON
    """
    client = None
    try:
        _log("info", f"[ONU_STATUS] Consultando ONUs em gpon-olt_{slot}/{card}/{pon}")
        client = get_olt_client(ip, port, username, password, protocol)
        client.connect()
        iface = _olt_iface(slot, card, pon)
        output = client.execute_command(f"show gpon onu state {iface}", timeout=30)
        onus = parse_onu_state(output)
        _log("info", f"[ONU_STATUS] {len(onus)} ONUs encontradas em {iface}")
        return onus
    except OLTConnectionError:
        raise
    except Exception as e:
        raise OLTConnectionError(f"Erro ao consultar ONUs: {str(e)}")
    finally:
        if client:
            try:
                client.disconnect()
            except Exception:
                pass


def get_onu_full_details(ip: str, port: int, username: str, password: str,
                         protocol: str, slot: int, card: int, pon: int,
                         onu_id: int, driver=None) -> Dict:
    """
    Obtém todos os detalhes de uma ONU específica.
    Aceita um driver opcional para suporte multi-modelo.
    Se driver=None, usa os comandos padrão ZTE C320.
    """
    client = None
    try:
        # Usa driver se fornecido, senão usa funções padrão
        if driver is not None:
            onu_ref = driver.onu_iface(f"{slot}/{card}/{pon}:{onu_id}")
            olt_ref = driver.olt_iface(slot, card, pon)
        else:
            onu_ref = _onu_iface(slot, card, pon, onu_id)
            olt_ref = _olt_iface(slot, card, pon)
        onu_idx = f"{slot}/{card}/{pon}:{onu_id}"

        _log("info", f"[ONU_FULL] Coletando detalhes de {onu_ref}")
        client = get_olt_client(ip, port, username, password, protocol)
        client.connect()

        result = {
            "onu_index":     onu_idx,
            "onu_interface": onu_ref,
            "olt_interface": olt_ref,
        }

        # 1. Estado detalhado
        cmd_detail = driver.cmd_onu_detail(onu_ref) if driver else f"show gpon onu detail-info {onu_ref}"
        _log("info", f"[ONU_FULL] {cmd_detail}")
        out = client.execute_command(cmd_detail, timeout=20)
        result["detail"] = parse_onu_detail(out, onu_idx)

        # 2. Potência e atenuação
        cmd_power = driver.cmd_onu_power(onu_ref) if driver else f"show pon power attenuation {onu_ref}"
        _log("info", f"[ONU_FULL] {cmd_power}")
        out = client.execute_command(cmd_power, timeout=15)
        _log("debug", f"[ONU_FULL] Output bruto power attenuation ({len(out)} chars): {repr(out[:500])}")
        result["power"] = parse_onu_power(out, onu_idx)
        _log("debug", f"[ONU_FULL] Power parsed: {result['power']}")

        # 3. Estado operacional (da lista da porta)
        cmd_state = driver.cmd_onu_state(olt_ref) if driver else f"show gpon onu state {olt_ref}"
        _log("info", f"[ONU_FULL] {cmd_state}")
        out = client.execute_command(cmd_state, timeout=20)
        all_states = driver.parse_onu_state(out) if driver else parse_onu_state(out)
        for s in all_states:
            if s["onu_index"] == onu_idx or s["onu_index"].endswith(f":{onu_id}"):
                result["status"] = s
                break

        _log("info", f"[ONU_FULL] Detalhes coletados com sucesso para {onu_ref}")
        return result

    except OLTConnectionError:
        raise
    except Exception as e:
        raise OLTConnectionError(f"Erro ao coletar detalhes da ONU: {str(e)}")
    finally:
        if client:
            try:
                client.disconnect()
            except Exception:
                pass


def reboot_onu(ip: str, port: int, username: str, password: str,
               protocol: str, slot: int, card: int, pon: int,
               onu_id: int, driver=None) -> Dict:
    """
    Reinicia uma ONU específica via Telnet/SSH.

    Sequência de comandos:
      1. pon-onu-mng gpon-onu_S/C/P:ID  (entra no modo de gerenciamento)
      2. reboot                           (solicita reboot)
      3. y                               (confirma)

    Funciona para C320 e C300/Titan (o driver gera a interface correta).
    """
    client = None
    try:
        if driver is not None:
            onu_ref = driver.onu_iface(f"{slot}/{card}/{pon}:{onu_id}")
            cmds = driver.cmd_onu_reboot(onu_ref)
        else:
            onu_ref = _onu_iface(slot, card, pon, onu_id)
            cmds = [f"pon-onu-mng {onu_ref}", "reboot", "y"]

        _log("info", f"[REBOOT] Iniciando reboot de {onu_ref} em {ip}")
        client = get_olt_client(ip, port, username, password, protocol)
        client.connect()

        # Passo 1: entrar no modo de gerenciamento
        _log("info", f"[REBOOT] Executando: {cmds[0]}")
        out1 = client.execute_command(cmds[0], timeout=10)
        _log("debug", f"[REBOOT] Resposta: {out1[:200]}")

        # Passo 2: solicitar reboot
        _log("info", f"[REBOOT] Executando: {cmds[1]}")
        out2 = client.execute_command(cmds[1], timeout=10)
        _log("debug", f"[REBOOT] Resposta: {out2[:200]}")

        # Passo 3: confirmar (pode precisar enviar direto sem aguardar prompt)
        # O C610/C300 exibe: "Confirm to reboot? [yes/no]:"
        if "confirm" in out2.lower() or "yes/no" in out2.lower() or not out2.strip():
            _log("info", f"[REBOOT] Confirmando com 'y'")
            # Envia 'y' diretamente no socket/telnet sem aguardar prompt completo
            if hasattr(client, 'tn'):
                client.tn.write(b"y\n")
                import time as _time
                _time.sleep(1.0)
                client.tn.read_very_eager(wait=0.5)
            elif hasattr(client, 'channel'):
                client.channel.send("y\n")
                import time as _time
                _time.sleep(1.0)
            out3 = "y"
        else:
            out3 = client.execute_command(cmds[2], timeout=10)

        _log("info", f"[REBOOT] Reboot enviado com sucesso para {onu_ref}")
        return {
            "success": True,
            "onu_interface": onu_ref,
            "message": f"Reboot enviado para {onu_ref}. A ONU será reiniciada em instantes.",
        }

    except OLTConnectionError as e:
        raise
    except Exception as e:
        _log("error", f"[REBOOT] Erro ao reiniciar {onu_ref}: {e}")
        raise OLTConnectionError(f"Erro ao reiniciar ONU: {str(e)}")
    finally:
        if client:
            try:
                client.disconnect()
            except Exception:
                pass


def get_onu_traffic(ip: str, port: int, username: str, password: str,
                    protocol: str, slot: int, card: int, pon: int,
                    onu_id: int, driver=None) -> Dict:
    """
    Coleta tráfego em tempo real de uma ONU via 'show interface gpon_onu-S/C/P:ID'.
    Retorna rx_bps, tx_bps, rx_pps, tx_pps, utilização e totais.
    """
    client = None
    try:
        if driver is not None:
            onu_ref = driver.onu_iface(f"{slot}/{card}/{pon}:{onu_id}")
            cmd = driver.cmd_onu_traffic(onu_ref)
        else:
            onu_ref = _onu_iface(slot, card, pon, onu_id)
            cmd = f"show interface {onu_ref}"

        _log("info", f"[TRAFFIC] Coletando tráfego de {onu_ref}")
        client = get_olt_client(ip, port, username, password, protocol)
        client.connect()
        out = client.execute_command(cmd, timeout=15)
        _log("debug", f"[TRAFFIC] Output bruto ({len(out)} chars): {out[:500]}")

        if driver is not None:
            traffic = driver.parse_onu_traffic(out)
        else:
            from .olt_driver import _parse_onu_traffic_common
            traffic = _parse_onu_traffic_common(out)

        _log("info", f"[TRAFFIC] Tráfego coletado: rx={traffic.get('rx_bps')} Bps, tx={traffic.get('tx_bps')} Bps")
        return {
            "success": True,
            "onu_interface": onu_ref,
            "traffic": traffic,
            "raw": out,
        }

    except OLTConnectionError:
        raise
    except Exception as e:
        _log("error", f"[TRAFFIC] Erro ao coletar tráfego: {e}")
        raise OLTConnectionError(f"Erro ao coletar tráfego da ONU: {str(e)}")
    finally:
        if client:
            try:
                client.disconnect()
            except Exception:
                pass
