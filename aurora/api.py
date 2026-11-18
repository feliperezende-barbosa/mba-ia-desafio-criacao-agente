from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from google.adk.flows.llm_flows.functions import REQUEST_CONFIRMATION_FUNCTION_CALL_NAME
from google.genai import types
from pydantic import BaseModel

from . import armazenamento as db
from . import config, runtime


class NovaSessao(BaseModel):
    apartamento: str


class NovaMensagem(BaseModel):
    texto: str


class RespostaConfirmacao(BaseModel):
    id: str
    confirmado: bool


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.inicializar()
    app.state.sessoes = runtime.criar_servico_sessoes()
    app.state.runner = runtime.criar_runner(app.state.sessoes)
    app.state.travas = runtime.TravasDeSessao()
    yield
    await app.state.runner.close()


app = FastAPI(title="Residencial Aurora", lifespan=lifespan)


async def _sessao(request: Request, session_id: str):
    sessao = await request.app.state.sessoes.get_session(
        app_name=config.APP_NAME, user_id=config.USER_ID, session_id=session_id
    )
    if sessao is None:
        raise HTTPException(status_code=404, detail="Sessão não encontrada.")
    return sessao


async def _resposta(request: Request, session_id: str, texto: str) -> dict:
    sessao = await _sessao(request, session_id)
    return {
        "resposta": texto,
        "confirmacoes_pendentes": runtime.confirmacoes_pendentes(sessao),
    }


@app.post("/sessoes", status_code=201)
async def criar_sessao(corpo: NovaSessao, request: Request):
    if corpo.apartamento not in db.apartamentos():
        raise HTTPException(status_code=422, detail="Apartamento não encontrado.")
    sessao = await request.app.state.sessoes.create_session(
        app_name=config.APP_NAME,
        user_id=config.USER_ID,
        state={"apartamento": corpo.apartamento},
    )
    return {"session_id": sessao.id}


@app.post("/sessoes/{session_id}/mensagens")
async def enviar_mensagem(session_id: str, corpo: NovaMensagem, request: Request):
    await _sessao(request, session_id)
    mensagem = types.Content(role="user", parts=[types.Part(text=corpo.texto)])
    async with request.app.state.travas.da_sessao(session_id):
        texto = await runtime.executar(request.app.state.runner, session_id, mensagem)
    return await _resposta(request, session_id, texto)


@app.post("/sessoes/{session_id}/confirmacoes")
async def responder_confirmacao(
    session_id: str, corpo: RespostaConfirmacao, request: Request
):
    await _sessao(request, session_id)
    async with request.app.state.travas.da_sessao(session_id):
        # Relê a sessão já dentro da trava: a confirmação pode ter sido respondida
        # por outra chamada enquanto esta esperava.
        sessao = await _sessao(request, session_id)
        autor = runtime.autor_da_confirmacao(sessao, corpo.id)
        if autor is None:
            raise HTTPException(
                status_code=409,
                detail="Não há confirmação pendente com esse id nesta sessão.",
            )
        resposta = types.Content(
            role="user",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        id=corpo.id,
                        name=REQUEST_CONFIRMATION_FUNCTION_CALL_NAME,
                        response={"confirmed": corpo.confirmado},
                    )
                )
            ],
        )
        texto = await runtime.executar(
            request.app.state.runner, session_id, resposta, agente=autor
        )
    return await _resposta(request, session_id, texto)


@app.get("/sessoes/{session_id}/eventos")
async def listar_eventos(session_id: str, request: Request):
    sessao = await _sessao(request, session_id)
    return [e.model_dump(mode="json", exclude_none=True) for e in sessao.events]


@app.get("/apartamentos/{apartamento}/reservas")
async def reservas_do_apartamento(apartamento: str):
    return db.listar_reservas(apartamento)


@app.get("/apartamentos/{apartamento}/visitantes")
async def visitantes_do_apartamento(apartamento: str):
    return db.listar_visitantes(apartamento)
