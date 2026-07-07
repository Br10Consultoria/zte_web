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
