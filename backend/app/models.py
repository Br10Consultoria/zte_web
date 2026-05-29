from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from datetime import datetime
from .database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    password_hash = Column(String(200), nullable=False)   # usado em auth.py como user.password_hash
    full_name = Column(String(100), nullable=True)
    email = Column(String(100), nullable=True)
    role = Column(String(20), default="viewer")           # admin | viewer
    is_active = Column(Boolean, default=True)
    totp_secret = Column(String(32), nullable=True)
    is_2fa_enabled = Column(Boolean, default=False)       # usado em auth.py como user.is_2fa_enabled
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
    snmp_community = Column(String(50), nullable=True)
    snmp_version = Column(String(5), default="2c")
    status = Column(String(20), default="unknown")
    model = Column(String(50), nullable=True)
    firmware = Column(String(50), nullable=True)
    last_check = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    ports = relationship("OLTPort", back_populates="olt", cascade="all, delete-orphan")


class OLTPort(Base):
    """
    Representa uma porta PON da OLT.
    Sintaxe ZTE Titan: gpon-olt_SLOT/PON (2 partes)
    Exemplo: gpon-olt_1/2 → slot=1, pon=2
    """
    __tablename__ = "olt_ports"

    id = Column(Integer, primary_key=True, index=True)
    olt_id = Column(Integer, ForeignKey("olts.id"), nullable=False)
    slot = Column(Integer, nullable=False)
    pon = Column(Integer, nullable=False)    # número da porta PON (gpon-olt_SLOT/PON)
    port_type = Column(String(20), default="gpon")
    description = Column(String(200), nullable=True)
    status = Column(String(20), default="unknown")
    onu_count = Column(Integer, default=0)
    discovered_at = Column(DateTime, default=datetime.utcnow)

    olt = relationship("OLT", back_populates="ports")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    username = Column(String(50), nullable=True)          # usado em routes/auth.py
    action = Column(String(100), nullable=False)
    resource = Column(String(100), nullable=True)
    details = Column(Text, nullable=True)
    ip_address = Column(String(45), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
