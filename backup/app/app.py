import os
import time
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import JSONResponse
from psycopg_pool import ConnectionPool

CONNINFO = (
    f"host={os.environ['PGHOST']} "
    f"port={os.environ['PGPORT']} "
    f"dbname={os.environ['PGDATABASE']} "
    f"user={os.environ['PGUSER']} "
    f"password={os.environ['PGPASSWORD']} "
    "connect_timeout=5"
)

POOL = ConnectionPool(
    CONNINFO,
    min_size=1,
    max_size=8,
    open=False,
    check=ConnectionPool.check_connection,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id   serial PRIMARY KEY,
    item text NOT NULL
)
"""


def ensure_schema(attempts: int = 30, delay: float = 2.0) -> None:
    for i in range(1, attempts + 1):
        try:
            with POOL.connection() as conn:
                conn.execute(SCHEMA)
            print(f"schema ready (attempt {i})", flush=True)
            return
        except psycopg.Error as exc:
            print(f"database not ready ({i}/{attempts}): {exc}", flush=True)
            time.sleep(delay)
    raise RuntimeError("database never became reachable")


@asynccontextmanager
async def lifespan(_: FastAPI):
    POOL.open()
    ensure_schema()
    yield
    POOL.close()


app = FastAPI(lifespan=lifespan)


def db_error(where: str, exc: Exception) -> JSONResponse:
    print(f"{where} failed: {exc}", flush=True)
    return JSONResponse({"ok": False, "error": str(exc)}, status_code=503)


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/readyz")
def readyz():
    try:
        with POOL.connection() as conn:
            conn.execute("SELECT 1")
    except psycopg.Error as exc:
        return db_error("readyz", exc)
    return {"ok": True}


@app.get("/")
def list_items():
    try:
        with POOL.connection() as conn:
            rows = conn.execute("SELECT id, item FROM items ORDER BY id").fetchall()
    except psycopg.Error as exc:
        return db_error("list", exc)
    return [{"id": r[0], "item": r[1]} for r in rows]


@app.post("/add")
def add(item: str = Form(...)):
    try:
        with POOL.connection() as conn:
            conn.execute("INSERT INTO items (item) VALUES (%s)", (item,))
    except psycopg.Error as exc:
        return db_error("add", exc)
    return {"ok": True}


@app.post("/delete/{item_id}")
def delete(item_id: int):
    try:
        with POOL.connection() as conn:
            deleted = conn.execute(
                "DELETE FROM items WHERE id = %s", (item_id,)
            ).rowcount
    except psycopg.Error as exc:
        return db_error("delete", exc)
    if deleted == 0:
        raise HTTPException(status_code=404, detail="no such item")
    return {"ok": True}
