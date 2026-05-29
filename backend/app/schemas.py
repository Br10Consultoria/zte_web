from pydantic import BaseModel, EmailStr
from typing import Optional, List
from datetime import datetime


# ============================================================
# AUTH SCHEMAS
# ============================================================

class UserCreate(BaseModel):
    username: str
    password: str
    email: Optional[str] = None
    full_name: Optional[str] = None
    role: Optional[str] = "viewer"


class UserUpdate(BaseModel):
    email: Optional[str] = None
    full_name: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None


class UserResponse(BaseModel):
    id: int
    username: str
    email: Optional[str] = None
    full_name: Optional[str] = None
    role: str
    is_2fa_enabled: bool
    is_active: bool
    created_at: datetime
    last_login: Optional[datetime] = None

    class Config:
        from_attributes = True


class LoginRequest(BaseModel):
    username: str
    password: str


class TwoFAVerify(BaseModel):
    totp_code: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    requires_2fa: bool = False
    user: Optional[UserResponse] = None


class TwoFASetupResponse(BaseModel):
    secret: str
    qr_code_url: str
    provisioning_uri: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


# ============================================================
# OLT SCHEMAS
# ============================================================

class OLTCreate(BaseModel):
    name: str
    ip: str
    port: int = 22
    username: str
    password: str
    protocol: str = "ssh"  # ssh, telnet, snmp
    snmp_community: Optional[str] = "public"
    snmp_version: Optional[str] = "2c"


class OLTUpdate(BaseModel):
    name: Optional[str] = None
    ip: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None
    protocol: Optional[str] = None
    snmp_community: Optional[str] = None
    snmp_version: Optional[str] = None


class OLTResponse(BaseModel):
    id: int
    name: str
    ip: str
    port: int
    username: str
    protocol: str
    snmp_community: Optional[str] = None
    status: str
    model: Optional[str] = None
    firmware: Optional[str] = None
    last_check: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


class OLTPortResponse(BaseModel):
    id: int
    olt_id: int
    slot: int
    card: int = 1
    port: int
    port_type: str
    description: Optional[str] = None
    status: str
    onu_count: int
    discovered_at: datetime

    class Config:
        from_attributes = True


# ============================================================
# ONU SCHEMAS
# ============================================================

class ONUStatus(BaseModel):
    onu_index: str
    admin_state: str
    oper_state: str
    last_down_cause: Optional[str] = None
    status_color: str = "gray"  # green, yellow, red, gray


class ONUDetail(BaseModel):
    onu_index: str
    serial_number: Optional[str] = None
    vendor_id: Optional[str] = None
    onu_type: Optional[str] = None
    run_state: Optional[str] = None
    omci_state: Optional[str] = None
    online_time: Optional[str] = None
    last_down_cause: Optional[str] = None
    fec: Optional[str] = None
    dba: Optional[str] = None


class ONUPower(BaseModel):
    onu_index: str
    rx_power: Optional[float] = None
    tx_power: Optional[float] = None
    attenuation: Optional[float] = None
    rx_status: str = "unknown"  # normal, warning, critical, unknown
    olt_rx_power: Optional[float] = None
    olt_rx_status: str = "unknown"


class ONUDistance(BaseModel):
    onu_index: str
    distance_m: Optional[int] = None


class ONUWanInfo(BaseModel):
    onu_index: str
    connection_type: Optional[str] = None
    status: Optional[str] = None
    ip_address: Optional[str] = None
    gateway: Optional[str] = None
    dns: Optional[str] = None


class ONUVoipStatus(BaseModel):
    onu_index: str
    status: Optional[str] = None


class ONUTemperature(BaseModel):
    onu_index: str
    temperature: Optional[float] = None
    temp_status: str = "unknown"  # normal, warning, critical, unknown


class ONUFirmware(BaseModel):
    onu_index: str
    current_version: Optional[str] = None
    active_version: Optional[str] = None
    backup_version: Optional[str] = None


class ONUFullInfo(BaseModel):
    status: Optional[ONUStatus] = None
    detail: Optional[ONUDetail] = None
    power: Optional[ONUPower] = None
    distance: Optional[ONUDistance] = None
    wan: Optional[ONUWanInfo] = None
    voip: Optional[ONUVoipStatus] = None
    temperature: Optional[ONUTemperature] = None
    firmware: Optional[ONUFirmware] = None
    cached: bool = False
    cache_time: Optional[str] = None


class PONStatusResponse(BaseModel):
    olt_id: int
    slot: int
    port: int
    onus: List[ONUStatus]
    cached: bool = False
    cache_time: Optional[str] = None
    olt_rx_power: Optional[str] = None
    olt_tx_power: Optional[str] = None
