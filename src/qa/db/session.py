"""Conexión a la base de datos."""
from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
import logging
from typing import Iterator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from qa.config import database_url
from qa.db.schema import Base


@lru_cache(maxsize=4)
def get_engine(url: str | None = None) -> Engine:
    url = url or database_url()
    if url.startswith("sqlite:///"):
        from pathlib import Path
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, future=True)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _):  # noqa: ANN001
            dbapi_conn.execute("PRAGMA foreign_keys=ON")
    return engine


log = logging.getLogger(__name__)
_MIGRATED: set[str] = set()


def _copy_expr(col) -> str:  # noqa: ANN001
    """Expresión de copia: columnas NOT NULL nulas en la tabla vieja reciben un valor neutro según su tipo."""
    if col.nullable or col.primary_key:
        return f'"{col.name}"'
    t = type(col.type).__name__.upper()
    neutral = {"JSON": "'[]'", "DATETIME": "CURRENT_TIMESTAMP", "DATE": "CURRENT_DATE", "FLOAT": "0",
               "INTEGER": "0", "BOOLEAN": "0"}.get(t, "''")
    return f'COALESCE("{col.name}", {neutral})'


def migrate(engine: Engine) -> list[str]:
    """Migración liviana e idempotente para bases existentes (SQLite):
    - agrega columnas nuevas del modelo que falten (ALTER TABLE ADD COLUMN);
    - reconstruye `datapoints` si su restricción única no incluye `known_at` (vintages).
    Otros motores: solo verifica y falla con un mensaje claro (usar una herramienta de migración)."""
    done = []
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in tables:
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name not in have:
                    if engine.dialect.name != "sqlite":
                        raise RuntimeError(f"falta la columna {table.name}.{col.name}: migrar la base")
                    conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" '
                                      f'{col.type.compile(engine.dialect)}'))
                    done.append(f"+{table.name}.{col.name}")
        if "datapoints" in tables and engine.dialect.name == "sqlite":
            ddl = conn.execute(text("SELECT sql FROM sqlite_master WHERE type='table' AND name='datapoints'")).scalar() or ""
            uq = ddl[ddl.find("uq_datapoint"):] if "uq_datapoint" in ddl else ""
            if uq and "known_at" not in uq.split(")")[0]:
                tcols = list(Base.metadata.tables["datapoints"].columns)
                cols = ", ".join(f'"{c.name}"' for c in tcols)
                sel = ", ".join(_copy_expr(c) for c in tcols)
                conn.execute(text("ALTER TABLE datapoints RENAME TO datapoints_old"))
                old_idx = conn.execute(text("SELECT name FROM sqlite_master WHERE type='index' AND "
                                            "tbl_name='datapoints_old' AND name NOT LIKE 'sqlite_autoindex%'")).all()
                for (idx,) in old_idx:
                    conn.execute(text(f'DROP INDEX "{idx}"'))     # los índices conservan su nombre tras el RENAME
                Base.metadata.tables["datapoints"].create(conn)
                conn.execute(text(f"INSERT INTO datapoints ({cols}) SELECT {sel} FROM datapoints_old"))
                conn.execute(text("DROP TABLE datapoints_old"))
                done.append("datapoints: uq_datapoint ahora incluye known_at (vintages)")
    if done:
        log.warning("migración aplicada: %s", done)
    return done


def init_db(url: str | None = None) -> Engine:
    engine = get_engine(url)
    key = str(engine.url)
    if key not in _MIGRATED:
        migrate(engine)
        _MIGRATED.add(key)
    Base.metadata.create_all(engine)
    return engine


@contextmanager
def session_scope(url: str | None = None) -> Iterator[Session]:
    engine = init_db(url)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    s = factory()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
