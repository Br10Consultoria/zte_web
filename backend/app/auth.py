from datetime import datetime, timedelta
from typing import Optional
import pyotp
import qrcode
import io
import base64
from jose import JWTError, jwt
import hashlib
import hmac
import os
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from .config import settings
from .database import get_db
from .models import User

security = HTTPBearer()


def get_password_hash(password: str) -> str:
    """Gera hash seguro com PBKDF2-SHA256 (nativo Python, sem dependencias externas)."""
    salt = os.urandom(32)
    key = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 260000)
    return salt.hex() + ':' + key.hex()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifica senha contra hash PBKDF2. Tambem aceita hashes bcrypt legados."""
    try:
        if ':' in hashed_password and not hashed_password.startswith('$'):
            # Formato PBKDF2: salt_hex:key_hex
            salt_hex, key_hex = hashed_password.split(':', 1)
            salt = bytes.fromhex(salt_hex)
            stored_key = bytes.fromhex(key_hex)
            new_key = hashlib.pbkdf2_hmac('sha256', plain_password.encode('utf-8'), salt, 260000)
            return hmac.compare_digest(stored_key, new_key)
        else:
            # Tenta bcrypt como fallback para hashes legados
            try:
                import bcrypt
                return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))
            except Exception:
                return False
    except Exception:
        return False


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return payload
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido ou expirado",
            headers={"WWW-Authenticate": "Bearer"},
        )


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> User:
    payload = decode_token(credentials.credentials)
    username: str = payload.get("sub")
    if username is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido")
    
    # Verificar se o 2FA foi completado
    two_fa_verified = payload.get("2fa_verified", False)
    user = db.query(User).filter(User.username == username).first()
    
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuário não encontrado ou inativo")
    
    if user.is_2fa_enabled and not two_fa_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Autenticação de dois fatores necessária"
        )
    
    return user


def get_current_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso restrito a administradores"
        )
    return current_user


def get_partial_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> User:
    """Retorna o usuário mesmo sem 2FA verificado (para o endpoint de verificação do 2FA)."""
    payload = decode_token(credentials.credentials)
    username: str = payload.get("sub")
    if username is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido")
    
    user = db.query(User).filter(User.username == username).first()
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuário não encontrado ou inativo")
    
    return user


# ============================================================
# TOTP / 2FA
# ============================================================

def generate_totp_secret() -> str:
    return pyotp.random_base32()


def get_totp_uri(secret: str, username: str, issuer: str = "ZTE Titan Manager") -> str:
    totp = pyotp.TOTP(secret)
    return totp.provisioning_uri(name=username, issuer_name=issuer)


def generate_qr_code_base64(provisioning_uri: str) -> str:
    """Gera o QR Code como string base64 para embutir no HTML."""
    qr = qrcode.QRCode(version=1, box_size=6, border=4)
    qr.add_data(provisioning_uri)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    img_base64 = base64.b64encode(buffer.getvalue()).decode("utf-8")
    return f"data:image/png;base64,{img_base64}"


def verify_totp(secret: str, code: str) -> bool:
    totp = pyotp.TOTP(secret)
    return totp.verify(code, valid_window=1)  # Aceita 1 janela de tolerância (30s)


# ============================================================
# INICIALIZAÇÃO DO ADMIN
# ============================================================

def create_default_admin(db: Session):
    """Cria o usuário admin padrão se não existir."""
    admin = db.query(User).filter(User.username == "admin").first()
    if not admin:
        admin = User(
            username="admin",
            email="admin@zte-titan.local",
            full_name="Administrador",
            password_hash=get_password_hash("Admin2024"),
            role="admin",
            is_active=True,
            is_2fa_enabled=False,
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)
        print("✅ Usuário admin criado: admin / Admin2024")
    return admin
