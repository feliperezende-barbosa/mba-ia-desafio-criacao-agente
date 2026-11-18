"""Armazenamento do condomínio (reservas e visitantes) em SQLite.

A exclusividade de reserva é garantida pelo banco: o índice único parcial
`ux_reserva_ativa` impede duas reservas ativas para a mesma área e data, e a
gravação roda em transação `BEGIN IMMEDIATE`, que serializa os escritores.
"""

import json
import sqlite3
from contextlib import contextmanager
from datetime import date

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS reservas (
    codigo TEXT PRIMARY KEY,
    apartamento TEXT NOT NULL,
    area TEXT NOT NULL,
    data TEXT NOT NULL,
    ativa INTEGER NOT NULL DEFAULT 1
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_reserva_ativa
    ON reservas (area, data) WHERE ativa = 1;
CREATE TABLE IF NOT EXISTS visitantes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    apartamento TEXT NOT NULL,
    nome TEXT NOT NULL,
    data TEXT NOT NULL,
    UNIQUE (apartamento, nome, data)
);
CREATE TABLE IF NOT EXISTS meta (chave TEXT PRIMARY KEY, valor TEXT NOT NULL);
"""

# Códigos novos começam acima de qualquer código dos dados iniciais.
PRIMEIRO_CODIGO = 5000


def _ler_json(nome: str) -> list[dict]:
    return json.loads((config.DADOS_DIR / nome).read_text(encoding="utf-8"))


def areas() -> dict[str, dict]:
    return {a["id"]: a for a in _ler_json("areas.json")}


def apartamentos() -> set[str]:
    return {a["numero"] for a in _ler_json("apartamentos.json")}


def data_valida(texto: str) -> bool:
    try:
        date.fromisoformat(texto)
    except (TypeError, ValueError):
        return False
    return len(texto) == 10


@contextmanager
def _conectar():
    con = sqlite3.connect(config.DB_CONDOMINIO, timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    try:
        yield con
    finally:
        con.close()


def _semear(con: sqlite3.Connection) -> None:
    for r in _ler_json("reservas.json"):
        con.execute(
            "INSERT INTO reservas (codigo, apartamento, area, data) VALUES (?, ?, ?, ?)",
            (r["codigo"], r["apartamento"], r["area"], r["data"]),
        )
    for v in _ler_json("visitantes.json"):
        con.execute(
            "INSERT INTO visitantes (apartamento, nome, data) VALUES (?, ?, ?)",
            (v["apartamento"], v["nome"], v["data"]),
        )
    con.execute("INSERT INTO meta (chave, valor) VALUES ('semeado', '1')")


def inicializar() -> None:
    """Cria o banco e carrega os dados iniciais se ainda não existirem."""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _conectar() as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(SCHEMA)
        con.execute("BEGIN IMMEDIATE")
        try:
            if con.execute("SELECT 1 FROM meta WHERE chave = 'semeado'").fetchone() is None:
                _semear(con)
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise


def restaurar() -> None:
    """Volta reservas e visitantes ao estado dos arquivos de `dados/`."""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _conectar() as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(SCHEMA)
        con.execute("BEGIN IMMEDIATE")
        try:
            con.execute("DELETE FROM reservas")
            con.execute("DELETE FROM visitantes")
            con.execute("DELETE FROM meta")
            _semear(con)
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise


def area_disponivel(area: str, data: str) -> bool:
    with _conectar() as con:
        linha = con.execute(
            "SELECT 1 FROM reservas WHERE area = ? AND data = ? AND ativa = 1",
            (area, data),
        ).fetchone()
    return linha is None


def reservar(apartamento: str, area: str, data: str) -> dict:
    """Grava a reserva. Devolve {"codigo": ...} ou {"codigo": None} se a data foi tomada."""
    with _conectar() as con:
        con.execute("BEGIN IMMEDIATE")
        try:
            # Inclui reservas canceladas: um código nunca é reaproveitado.
            maior = con.execute(
                "SELECT MAX(CAST(SUBSTR(codigo, 5) AS INTEGER)) FROM reservas"
            ).fetchone()[0]
            codigo = f"RSV-{max(maior or 0, PRIMEIRO_CODIGO) + 1}"
            con.execute(
                "INSERT INTO reservas (codigo, apartamento, area, data) VALUES (?, ?, ?, ?)",
                (codigo, apartamento, area, data),
            )
            con.execute("COMMIT")
        except sqlite3.IntegrityError:
            con.execute("ROLLBACK")
            return {"codigo": None}
        except Exception:
            con.execute("ROLLBACK")
            raise
    return {"codigo": codigo}


def cancelar_reserva(apartamento: str, area: str, data: str) -> str | None:
    """Cancela a reserva ativa do apartamento. Devolve o código ou None."""
    with _conectar() as con:
        con.execute("BEGIN IMMEDIATE")
        try:
            linha = con.execute(
                "SELECT codigo FROM reservas"
                " WHERE apartamento = ? AND area = ? AND data = ? AND ativa = 1",
                (apartamento, area, data),
            ).fetchone()
            if linha is not None:
                con.execute(
                    "UPDATE reservas SET ativa = 0 WHERE codigo = ?", (linha["codigo"],)
                )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    return linha["codigo"] if linha else None


def listar_reservas(apartamento: str) -> list[dict]:
    with _conectar() as con:
        linhas = con.execute(
            "SELECT codigo, area, data FROM reservas"
            " WHERE apartamento = ? AND ativa = 1 ORDER BY data, codigo",
            (apartamento,),
        ).fetchall()
    return [dict(l) for l in linhas]


def autorizar_visitante(apartamento: str, nome: str, data: str) -> bool:
    """Registra a autorização. Devolve False se ela já existia."""
    with _conectar() as con:
        try:
            con.execute(
                "INSERT INTO visitantes (apartamento, nome, data) VALUES (?, ?, ?)",
                (apartamento, nome, data),
            )
        except sqlite3.IntegrityError:
            return False
    return True


def listar_visitantes(apartamento: str) -> list[dict]:
    with _conectar() as con:
        linhas = con.execute(
            "SELECT nome, data FROM visitantes WHERE apartamento = ? ORDER BY data, id",
            (apartamento,),
        ).fetchall()
    return [dict(l) for l in linhas]
