from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from datetime import datetime
from .database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    email = Column(String(100), unique=True, index=True, nullable=True)
    full_name = Column(String(100), nullable=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), default="viewer", nullable=False)  # admin ou viewer
    totp_secret = Column(String(64), nullable=True)
    is_2fa_enabled = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login = Column(DateTime, nullable=True)


class OLT(Base):
    __tablename__ = "olts"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    ip = Column(String(45), nullable=False)
    port = Column(Integer, default=22)
    username = Column(String(50), nullable=False)
    password = Column(String(255), nullable=False)
    protocol = Column(String(10), default="ssh")  # ssh, telnet, snmp
    snmp_community = Column(String(100), nullable=True)
    snmp_version = Column(String(5), default="2c")
    status = Column(String(20), default="unknown")  # online, offline, unknown
    model = Column(String(50), nullable=True)
    firmware = Column(String(100), nullable=True)
    last_check = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    ports = relationship("OLTPort", back_populates="olt", cascade="all, delete-orphan")


class OLTPort(Base):
    __tablename__ = "olt_ports"

    id = Column(Integer, primary_key=True, index=True)
    olt_id = Column(Integer, ForeignKey("olts.id"), nullable=False)
    slot = Column(Integer, nullable=False)
    card = Column(Integer, default=1, nullable=False)  # subslot (gpon-olt_SLOT/CARD/PORT)
    port = Column(Integer, nullable=False)
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
    username = Column(String(50), nullable=True)
    action = Column(String(100), nullable=False)
    resource = Column(String(200), nullable=True)
    ip_address = Column(String(45), nullable=True)
    details = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
