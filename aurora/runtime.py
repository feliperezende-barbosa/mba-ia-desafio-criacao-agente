"""Runner, sessões persistidas e ponte entre a API e as confirmações do ADK."""

import asyncio
import contextvars
import sqlite3

from google.adk.flows.llm_flows.functions import REQUEST_CONFIRMATION_FUNCTION_CALL_NAME
from google.adk.runners import Runner
from google.adk.sessions.sqlite_session_service import SqliteSessionService
from google.genai import types

from . import config
from .agents import app
from .tools import resolver_area
from . import armazenamento as db

# Agente que deve receber a resposta de uma confirmação (ver AuroraRunner).
_agente_forcado: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "agente_forcado", default=None
)


class AuroraRunner(Runner):
    """Runner que entrega a resposta de uma confirmação ao agente que a pediu.

    O Runner escolhe o agente que continua a sessão olhando os eventos; essa
    escolha muda conforme a topologia e a configuração de retomada, e se a
    resposta chegar a outro agente a ação simplesmente não executa. Aqui a rota
    de confirmações indica o autor do pedido, e o Runner só decide sozinho nas
    mensagens de texto.
    """

    def _find_agent_to_run(self, session, root_agent):
        nome = _agente_forcado.get()
        if nome:
            agente = root_agent.find_agent(nome)
            if agente is not None:
                return agente
        return super()._find_agent_to_run(session, root_agent)


def criar_servico_sessoes() -> SqliteSessionService:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    # WAL evita "database is locked" quando sessões diferentes gravam juntas.
    with sqlite3.connect(config.DB_SESSOES) as con:
        con.execute("PRAGMA journal_mode=WAL")
    return SqliteSessionService(db_path=str(config.DB_SESSOES))


def criar_runner(servico_sessoes: SqliteSessionService) -> AuroraRunner:
    return AuroraRunner(app=app, session_service=servico_sessoes)


def _pedidos_de_confirmacao(session) -> dict[str, tuple[types.FunctionCall, str]]:
    """Confirmações pedidas e ainda sem resposta: id -> (chamada, autor)."""
    pedidas: dict[str, tuple[types.FunctionCall, str]] = {}
    respondidas: set[str] = set()
    for evento in session.events:
        for chamada in evento.get_function_calls():
            if chamada.name == REQUEST_CONFIRMATION_FUNCTION_CALL_NAME and chamada.id:
                pedidas[chamada.id] = (chamada, evento.author)
        for resposta in evento.get_function_responses():
            if resposta.name == REQUEST_CONFIRMATION_FUNCTION_CALL_NAME and resposta.id:
                respondidas.add(resposta.id)
    return {i: v for i, v in pedidas.items() if i not in respondidas}


def confirmacoes_pendentes(session) -> list[dict]:
    pendentes = []
    for id_, (chamada, _autor) in _pedidos_de_confirmacao(session).items():
        original = (chamada.args or {}).get("originalFunctionCall") or {}
        detalhes = dict(original.get("args") or {})
        if "area" in detalhes:
            area_id = resolver_area(str(detalhes["area"]))
            if area_id is not None:
                detalhes["area"] = area_id
                detalhes["taxa"] = db.areas()[area_id]["taxa"]
        pendentes.append(
            {"id": id_, "acao": original.get("name", ""), "detalhes": detalhes}
        )
    return pendentes


def autor_da_confirmacao(session, id_: str) -> str | None:
    pedido = _pedidos_de_confirmacao(session).get(id_)
    return pedido[1] if pedido else None


def _texto_final(evento) -> str:
    if evento.author == "user" or not evento.is_final_response():
        return ""
    if not evento.content or not evento.content.parts:
        return ""
    return "".join(
        p.text for p in evento.content.parts if p.text and not getattr(p, "thought", False)
    ).strip()


async def executar(
    runner: Runner,
    session_id: str,
    mensagem: types.Content,
    agente: str | None = None,
) -> str:
    """Roda uma rodada do runner e devolve o texto da resposta final."""
    token = _agente_forcado.set(agente)
    textos: list[str] = []
    try:
        async for evento in runner.run_async(
            user_id=config.USER_ID, session_id=session_id, new_message=mensagem
        ):
            if texto := _texto_final(evento):
                textos.append(texto)
    finally:
        _agente_forcado.reset(token)
    return textos[-1] if textos else ""


class TravasDeSessao:
    """Uma execução por vez em cada sessão (o ADK recusa sessões desatualizadas)."""

    def __init__(self) -> None:
        self._travas: dict[str, asyncio.Lock] = {}

    def da_sessao(self, session_id: str) -> asyncio.Lock:
        return self._travas.setdefault(session_id, asyncio.Lock())
