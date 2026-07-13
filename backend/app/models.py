from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from datetime import datetime
from .database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    password_hash = Column(String(200), nullable=False)
    full_name = Column(String(100), nullable=True)
    email = Column(String(100), nullable=True)
    role = Column(String(20), default="viewer")           # admin | viewer
    is_active = Column(Boolean, default=True)
    totp_secret = Column(String(32), nullable=True)
    is_2fa_enabled = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login = Column(DateTime, nullable=True)


class OLT(Base):
    __tablename__ = "olts"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    ip = Column(String(45), nullable=False)
    port = Column(Integer, default=23)
    username = Column(String(50), nullable=False)
    password = Column(String(100), nullable=False)
    protocol = Column(String(10), default="telnet")       # telnet | ssh
    snmp_community = Column(String(50), nullable=True, default="public")
    snmp_version = Column(String(5), default="2c")
    status = Column(String(20), default="unknown")
    model = Column(String(50), nullable=True)
    olt_model = Column(String(30), nullable=True, default="zte_c600")  # chave do driver: zte_c600, zte_c300
    firmware = Column(String(50), nullable=True)
    last_check = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    ports = relationship("OLTPort", back_populates="olt", cascade="all, delete-orphan")


class OLTPort(Base):
    """
    Representa uma porta PON da OLT.
    Sintaxe ZTE Titan: gpon-olt_SLOT/CARD/PON (3 partes)
    Exemplo: gpon-olt_1/2/3 → slot=1, card=2, pon=3
    """
    __tablename__ = "olt_ports"

    id = Column(Integer, primary_key=True, index=True)
    olt_id = Column(Integer, ForeignKey("olts.id"), nullable=False)
    slot = Column(Integer, nullable=False)
    card = Column(Integer, nullable=False, default=1)    # subslot/card (ex: 2 em gpon-olt_1/2/3)
    pon = Column(Integer, nullable=False)                # número da porta PON
    port_type = Column(String(20), default="gpon")
    description = Column(String(200), nullable=True)
    status = Column(String(20), default="unknown")
    onu_count = Column(Integer, default=0)
    onu_max = Column(Integer, default=128)   # capacidade máxima da PON
    discovered_at = Column(DateTime, default=datetime.utcnow)

    olt = relationship("OLT", back_populates="ports")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    username = Column(String(50), nullable=True)
    action = Column(String(100), nullable=False)
    resource = Column(String(100), nullable=True)
    details = Column(Text, nullable=True)
    ip_address = Column(String(45), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class BackupSettings(Base):
    __tablename__ = "backup_settings"

    id = Column(Integer, primary_key=True, default=1)
    server_ip = Column(String(45), nullable=True)
    ftp_bind_host = Column(String(45), default="0.0.0.0")
    ftp_port = Column(Integer, default=21)
    ftp_passive_ports = Column(String(50), default="30000-30009")
    ftp_user = Column(String(80), default="ztebackup")
    ftp_password = Column(String(120), nullable=True)
    source_path = Column(String(200), default="/datadisk0/DATA0/startrun.dat")
    telegram_bot_token = Column(String(200), nullable=True)
    telegram_chat_id = Column(String(80), nullable=True)
    telegram_enabled = Column(Boolean, default=True)
    keep_local = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class BackupJob(Base):
    __tablename__ = "backup_jobs"

    id = Column(Integer, primary_key=True, index=True)
    olt_id = Column(Integer, ForeignKey("olts.id"), nullable=False)
    status = Column(String(20), default="running")  # running | success | failed
    started_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)
    filename = Column(String(255), nullable=True)
    file_path = Column(String(500), nullable=True)
    file_size = Column(Integer, nullable=True)
    sha256 = Column(String(64), nullable=True)
    telegram_sent = Column(Boolean, default=False)
    message = Column(Text, nullable=True)
    command_output = Column(Text, nullable=True)

    olt = relationship("OLT")


class ProvisionTemplate(Base):
    __tablename__ = "provision_templates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    model_alias = Column(String(100), nullable=True)
    vlan = Column(Integer, default=1)
    onu_type = Column(String(80), default="ZTE-F601")
    start_onu_number = Column(Integer, default=1)
    commands = Column(Text, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ONUAnnotation(Base):
    __tablename__ = "onu_annotations"

    id = Column(Integer, primary_key=True, index=True)
    olt_id = Column(Integer, ForeignKey("olts.id"), nullable=False)
    slot = Column(Integer, nullable=False)
    card = Column(Integer, nullable=False, default=1)
    pon = Column(Integer, nullable=False)
    onu_id = Column(Integer, nullable=False)
    operation_mode = Column(String(20), nullable=True)  # bridge | router | auto
    comment = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
