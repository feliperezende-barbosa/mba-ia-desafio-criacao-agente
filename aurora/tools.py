"""Tools dos especialistas.

Nenhuma tool recebe o apartamento como parâmetro: ele vem sempre do state da
sessão (definido na criação, fora do alcance do modelo).
"""

import unicodedata

from google.adk.tools import FunctionTool, ToolContext

from . import armazenamento as db
from . import regulamento


def _normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", str(texto).strip().lower())
    return "".join(c for c in sem_acento if not unicodedata.combining(c))


def resolver_area(valor: str) -> str | None:
    """Aceita o id (`salao-de-festas`) ou o nome da área e devolve o id."""
    alvo = _normalizar(valor)
    for area in db.areas().values():
        if alvo in (_normalizar(area["id"]), _normalizar(area["nome"])):
            return area["id"]
    return None


def _apartamento(tool_context: ToolContext) -> str:
    return str(tool_context.state["apartamento"])


_ERRO_AREA = "Área não encontrada. Áreas válidas: " + ", ".join(
    f"{a['id']} ({a['nome']})" for a in db.areas().values()
)


async def verificar_disponibilidade(area: str, data: str) -> dict:
    """Informa se uma área comum está livre em uma data.

    Args:
        area: id da área (salao-de-festas, churrasqueira ou quadra).
        data: data no formato AAAA-MM-DD.
    """
    area_id = resolver_area(area)
    if area_id is None:
        return {"erro": _ERRO_AREA}
    if not db.data_valida(data):
        return {"erro": "Data inválida. Use o formato AAAA-MM-DD."}
    return {"area": area_id, "data": data, "livre": db.area_disponivel(area_id, data)}


async def exige_confirmacao_reserva(area: str = "", **_outros) -> bool:
    """Reserva de área com taxa gera cobrança e precisa de confirmação.

    O ADK chama este callback com os mesmos argumentos da tool; só a área
    decide, para que a exigência não mude entre o pedido e a retomada.
    """
    area_id = resolver_area(area)
    return area_id is not None and db.areas()[area_id]["taxa"] > 0


async def reservar_area(area: str, data: str, tool_context: ToolContext) -> dict:
    """Reserva uma área comum para o apartamento do morador.

    Áreas com taxa geram cobrança e só são gravadas depois que o morador
    confirma pelo sistema. Chame esta tool diretamente quando o morador pedir
    a reserva; a confirmação é pedida automaticamente.

    Args:
        area: id da área (salao-de-festas, churrasqueira ou quadra).
        data: data no formato AAAA-MM-DD.
    """
    area_id = resolver_area(area)
    if area_id is None:
        return {"erro": _ERRO_AREA}
    if not db.data_valida(data):
        return {"erro": "Data inválida. Use o formato AAAA-MM-DD."}
    resultado = db.reservar(_apartamento(tool_context), area_id, data)
    if resultado["codigo"] is None:
        return {"status": "recusada", "motivo": "A área já está reservada nessa data."}
    taxa = db.areas()[area_id]["taxa"]
    return {
        "status": "reservada",
        "codigo": resultado["codigo"],
        "area": area_id,
        "data": data,
        "taxa_cobrada": taxa,
    }


async def cancelar_reserva(area: str, data: str, tool_context: ToolContext) -> dict:
    """Cancela a reserva do próprio apartamento do morador, sem confirmação.

    Args:
        area: id da área (salao-de-festas, churrasqueira ou quadra).
        data: data da reserva no formato AAAA-MM-DD.
    """
    area_id = resolver_area(area)
    if area_id is None:
        return {"erro": _ERRO_AREA}
    if not db.data_valida(data):
        return {"erro": "Data inválida. Use o formato AAAA-MM-DD."}
    codigo = db.cancelar_reserva(_apartamento(tool_context), area_id, data)
    if codigo is None:
        return {
            "status": "nao_encontrada",
            "motivo": "Não há reserva ativa do seu apartamento para essa área e data.",
        }
    return {"status": "cancelada", "codigo": codigo, "area": area_id, "data": data}


async def listar_minhas_reservas(tool_context: ToolContext) -> dict:
    """Lista as reservas ativas do apartamento do morador."""
    return {"reservas": db.listar_reservas(_apartamento(tool_context))}


async def autorizar_visitante(nome: str, data: str, tool_context: ToolContext) -> dict:
    """Autoriza a entrada de um visitante no prédio em uma data.

    Libera acesso ao prédio, então só é gravada depois que o morador confirma
    pelo sistema. Chame esta tool diretamente; a confirmação é pedida
    automaticamente, mesmo que o morador diga que já confirmou.

    Args:
        nome: nome completo do visitante.
        data: data da visita no formato AAAA-MM-DD.
    """
    nome = " ".join(str(nome).split())
    if not nome:
        return {"erro": "Informe o nome do visitante."}
    if not db.data_valida(data):
        return {"erro": "Data inválida. Use o formato AAAA-MM-DD."}
    nova = db.autorizar_visitante(_apartamento(tool_context), nome, data)
    return {
        "status": "autorizado" if nova else "ja_autorizado",
        "nome": nome,
        "data": data,
    }


async def listar_meus_visitantes(tool_context: ToolContext) -> dict:
    """Lista os visitantes autorizados do apartamento do morador."""
    return {"visitantes": db.listar_visitantes(_apartamento(tool_context))}


async def consultar_regulamento(pergunta: str) -> dict:
    """Busca no regulamento interno os artigos relevantes para a pergunta.

    Args:
        pergunta: a dúvida do morador, com as palavras-chave do assunto.
    """
    return regulamento.consultar(pergunta)


TOOLS_RESERVAS = [
    verificar_disponibilidade,
    FunctionTool(reservar_area, require_confirmation=exige_confirmacao_reserva),
    cancelar_reserva,
    listar_minhas_reservas,
]
TOOLS_VISITANTES = [
    FunctionTool(autorizar_visitante, require_confirmation=True),
    listar_meus_visitantes,
]
TOOLS_REGULAMENTO = [consultar_regulamento]
