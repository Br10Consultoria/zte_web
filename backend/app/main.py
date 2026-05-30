from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
import os
import logging
import logging.handlers

from .config import settings


def _setup_logging():
    """
    Configura logging detalhado em arquivo para acompanhamento em tempo real.
    Arquivo: /opt/zte_titan/data/zte_titan.log (rotativo, max 10MB x 5 arquivos)
    """
    log_dir = os.path.join(os.path.dirname(__file__), "..", "..", "data")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "zte_titan.log")

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Handler rotativo (10MB x 5 arquivos)
    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    file_handler.setLevel(logging.DEBUG)

    # Handler para console (INFO)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)
    console_handler.setLevel(logging.INFO)

    # Configura loggers do sistema
    for name in ["olt_client", "snmp_client", "routes.olts", "routes.onus", "routes.auth"]:
        lg = logging.getLogger(name)
        lg.setLevel(logging.DEBUG)
        if not lg.handlers:
            lg.addHandler(file_handler)
            lg.addHandler(console_handler)
        lg.propagate = False

    # Logger raiz para capturar uvicorn/fastapi
    root = logging.getLogger()
    if not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers):
        root.addHandler(file_handler)
    root.setLevel(logging.INFO)

    print(f"\u2705 Logs em: {os.path.abspath(log_file)}")
    return log_file


_LOG_FILE = _setup_logging()
from .database import init_db, SessionLocal
from .auth import create_default_admin
from .routes import auth, olts, onus

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Sistema de Gerenciamento de ONUs ZTE Titan - API REST",
    docs_url="/api/docs",
    redoc_url="/api/redoc"
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Handler global de exceções — garante que erros inesperados retornem JSON
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    import traceback
    tb = traceback.format_exc()
    logging.getLogger("main").error(f"[UNHANDLED] {request.url}: {exc}\n{tb}")
    return JSONResponse(
        status_code=500,
        content={"detail": f"Erro interno do servidor: {str(exc)}"}
    )

# Rotas da API
app.include_router(auth.router, prefix="/api")
app.include_router(olts.router, prefix="/api")
app.include_router(onus.router, prefix="/api")


@app.get("/api/logs")
async def get_logs(
    lines: int = 100,
    current_user = None
):
    """Retorna as últimas N linhas do arquivo de log."""
    from fastapi.responses import PlainTextResponse
    try:
        with open(_LOG_FILE, "r", encoding="utf-8") as f:
            all_lines = f.readlines()
        last_lines = all_lines[-lines:]
        return PlainTextResponse("".join(last_lines))
    except FileNotFoundError:
        return PlainTextResponse("Arquivo de log não encontrado ainda.")
    except Exception as e:
        return PlainTextResponse(f"Erro ao ler log: {e}")


@app.get("/api/health")
def health_check():
    from .redis_client import cache
    return {
        "status": "ok",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "redis": cache.is_available()
    }


# Servir arquivos estáticos do frontend
frontend_path = os.path.join(os.path.dirname(__file__), "..", "..", "frontend")
if os.path.exists(frontend_path):
    app.mount("/static", StaticFiles(directory=os.path.join(frontend_path, "static")), name="static")

    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        index_file = os.path.join(frontend_path, "index.html")
        if os.path.exists(index_file):
            return FileResponse(index_file)
        return {"error": "Frontend não encontrado"}


@app.on_event("startup")
def startup_event():
    print(f"🚀 Iniciando {settings.APP_NAME} v{settings.APP_VERSION}")
    init_db()
    _migrate_db()
    db = SessionLocal()
    try:
        create_default_admin(db)
    finally:
        db.close()
    print("✅ Sistema iniciado com sucesso!")
    print(f"📖 Documentação API: http://localhost:8000/api/docs")


def _migrate_db():
    """
    Aplica migrações incrementais no banco SQLite sem perder dados.
    """
    from .database import engine
    import sqlalchemy as sa

    with engine.connect() as conn:
        # --- Migração tabela users ---
        try:
            result = conn.execute(sa.text("PRAGMA table_info(users)"))
            ucols = {row[1] for row in result.fetchall()}

            # Renomeia hashed_password → password_hash
            if "hashed_password" in ucols and "password_hash" not in ucols:
                conn.execute(sa.text("ALTER TABLE users ADD COLUMN password_hash VARCHAR(200)"))
                conn.execute(sa.text("UPDATE users SET password_hash = hashed_password"))
                conn.commit()
                print("✅ Migração users: password_hash criado")

            # Renomeia totp_enabled → is_2fa_enabled
            if "totp_enabled" in ucols and "is_2fa_enabled" not in ucols:
                conn.execute(sa.text("ALTER TABLE users ADD COLUMN is_2fa_enabled BOOLEAN DEFAULT 0"))
                conn.execute(sa.text("UPDATE users SET is_2fa_enabled = totp_enabled"))
                conn.commit()
                print("✅ Migração users: is_2fa_enabled criado")

            # Garante is_2fa_enabled existe
            if "is_2fa_enabled" not in ucols and "totp_enabled" not in ucols:
                conn.execute(sa.text("ALTER TABLE users ADD COLUMN is_2fa_enabled BOOLEAN DEFAULT 0"))
                conn.commit()

            # Garante password_hash existe
            if "password_hash" not in ucols and "hashed_password" not in ucols:
                conn.execute(sa.text("ALTER TABLE users ADD COLUMN password_hash VARCHAR(200)"))
                conn.commit()

        except Exception as e:
            print(f"⚠️  Migração users: {e}")

        # --- Migração tabela olt_ports ---
        try:
            result = conn.execute(sa.text("PRAGMA table_info(olt_ports)"))
            cols = {row[1] for row in result.fetchall()}

            # Se ainda tem a coluna 'port' antiga (sem 'pon'), migra
            if "port" in cols and "pon" not in cols:
                conn.execute(sa.text("ALTER TABLE olt_ports ADD COLUMN pon INTEGER NOT NULL DEFAULT 1"))
                conn.execute(sa.text("UPDATE olt_ports SET pon = port"))
                conn.commit()
                print("✅ Migração: coluna 'pon' criada a partir de 'port'")

            # Garante que pon existe
            elif "pon" not in cols:
                conn.execute(sa.text("ALTER TABLE olt_ports ADD COLUMN pon INTEGER NOT NULL DEFAULT 1"))
                conn.commit()
                print("✅ Migração: coluna 'pon' adicionada")

            # Garante que card existe (adicionado para suporte a SLOT/CARD/PON)
            if "card" not in cols:
                conn.execute(sa.text("ALTER TABLE olt_ports ADD COLUMN card INTEGER NOT NULL DEFAULT 1"))
                conn.commit()
                print("✅ Migração: coluna 'card' adicionada")

        except Exception as e:
            print(f"⚠️  Migração olt_ports: {e}")
