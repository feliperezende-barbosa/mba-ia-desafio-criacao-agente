# Residencial Aurora: assistente virtual com Google ADK

API em Python (FastAPI) com um assistente de condomínio construído com Google ADK 2.9.1. O morador reserva áreas comuns, cancela as próprias reservas, autoriza visitantes e tira dúvidas do regulamento. As regras críticas ficam no código, não no prompt. O enunciado original está em [docs/enunciado.md](docs/enunciado.md).

## Arquitetura

```
POST /sessoes/{id}/mensagens ─► Runner (AuroraRunner)
                                   │
                                   ▼
                       aurora (agente principal, sem tools)
                          │ transfer_to_agent
          ┌───────────────┼────────────────┐
          ▼               ▼                ▼
      reservas        visitantes      regulamento
   (tools de agenda)  (tools de acesso) (consulta por artigo)
          └───────────────┴────────────────┘
                          ▼
        SQLite: condominio.db (reservas, visitantes) e sessoes.db (sessões ADK)
```

| Agente | Responsabilidade | Como é acionado | Por quê |
| --- | --- | --- | --- |
| `aurora` ([agents.py](aurora/agents.py)) | Entender o pedido e encaminhar. Não tem tools nem o regulamento. | Raiz do App. | Mantém o contexto do principal pequeno e sem poder de gravação. |
| `reservas` | Disponibilidade, reservar, cancelar e listar reservas. | `sub_agents` do principal, por `transfer_to_agent`. | A reserva com taxa pede confirmação; o agente que pede é o que precisa receber a resposta, e a transferência mantém isso no mesmo agente. |
| `visitantes` | Autorizar e listar visitantes. | Idem. | Autorizar libera acesso e sempre pede confirmação. |
| `regulamento` | Responder dúvidas com trechos consultados. | Idem. | Isola o regulamento: só este agente chama a tool que o consulta. |

Todos os especialistas podem transferir de volta ao principal e aos pares (padrão do ADK). Isso mantém a conversa roteável nas mensagens seguintes. Os modelos são Gemini (`gemini-2.5-flash` por padrão, mudável por `AURORA_MODEL`).

Armazenamento: dois arquivos SQLite em `.data/` (fora do Git). `condominio.db` guarda reservas e visitantes; `sessoes.db` guarda as sessões do ADK (`SqliteSessionService`).

## Garantias

### 1. Cobrança ou acesso só com confirmação

- [aurora/tools.py](aurora/tools.py): `FunctionTool(reservar_area, require_confirmation=exige_confirmacao_reserva)` pede confirmação quando a taxa da área é maior que zero (decide só pela área, em `exige_confirmacao_reserva`). `FunctionTool(autorizar_visitante, require_confirmation=True)` sempre pede.
- A confirmação é do ADK (`adk_request_confirmation`): a tool não executa até chegar um `FunctionResponse` do usuário. Texto como "já confirmei" não tem efeito, porque só a rota de confirmações produz essa resposta.
- [aurora/runtime.py](aurora/runtime.py): `confirmacoes_pendentes` lê os eventos da sessão (pedidos sem resposta) e monta `id`, `acao` e `detalhes` (argumentos da chamada original, com a área normalizada e a taxa).
- [aurora/api.py](aurora/api.py): `responder_confirmacao` só aceita um `id` que esteja pendente naquela sessão; qualquer outro, inclusive um já respondido, devolve `409` e nada executa. Negar faz a tool devolver "rejeitada" sem gravar.
- Retomada: `AuroraRunner._find_agent_to_run` ([runtime.py](aurora/runtime.py)) entrega a resposta ao agente que pediu a confirmação (o autor do evento de pedido). Sem isso, a escolha do Runner depende da topologia e a ação pode não executar sem erro.

### 2. Cada sessão pertence a um apartamento

- [aurora/api.py](aurora/api.py): `criar_sessao` grava `state={"apartamento": ...}` uma única vez.
- [aurora/tools.py](aurora/tools.py): `_apartamento(tool_context)` lê `tool_context.state["apartamento"]`. Nenhuma tool tem parâmetro de apartamento, então o modelo não tem como escolher outro.
- `verificar_disponibilidade` devolve só `livre` verdadeiro ou falso; `cancelar_reserva` só enxerga reservas do apartamento da sessão e responde igual para "não existe" e "é de outro". Dados de outros apartamentos nunca entram em resposta de tool, logo não entram nos eventos.
- Os prompts ([agents.py](aurora/agents.py)) reforçam a recusa, mas a garantia não depende deles.

### 3. Nada se perde no reinício

- [aurora/runtime.py](aurora/runtime.py): `criar_servico_sessoes` usa `SqliteSessionService` em `.data/sessoes.db`; [armazenamento.py](aurora/armazenamento.py) guarda reservas e visitantes em `.data/condominio.db`.
- A subida (`lifespan` em [api.py](aurora/api.py)) só semeia os dados se o banco ainda não existir; reiniciar não restaura nada. Códigos de reserva vêm do maior código já gravado, inclusive de reservas canceladas (cancelar só marca `ativa = 0`), então nunca se repetem.

### 4. O regulamento é consultado, não carregado

- [aurora/regulamento.py](aurora/regulamento.py): `consultar` escolhe o capítulo mais adequado à pergunta e devolve no máximo 3 artigos dele. O texto completo fica em memória no processo, fora do contexto.
- [aurora/tools.py](aurora/tools.py): `consultar_regulamento` é tool só do agente `regulamento`. O principal e os outros especialistas não recebem o regulamento nas instruções, e a tool devolve apenas artigos de um capítulo, então os eventos não trazem capítulos de outros assuntos.

### 5. Dois moradores, uma reserva

- [aurora/armazenamento.py](aurora/armazenamento.py): o índice único parcial `ux_reserva_ativa` em `reservas (area, data) WHERE ativa = 1` e a transação `BEGIN IMMEDIATE` em `reservar` fazem a exclusividade valer no instante da gravação, sem depender da checagem prévia.
- Quem perde a disputa recebe `{"status": "recusada"}` da tool, que o agente explica ao morador com `200`, sem erro de servidor.

## Como rodar

Pré-requisitos: Python 3.12 ou superior, [uv](https://docs.astral.sh/uv/) e uma chave do Google AI Studio.

```bash
cp .env.example .env      # preencha GOOGLE_API_KEY
uv sync
uv run python -m aurora.restaurar     # restaura os dados iniciais (com a API parada)
uv run uvicorn aurora.api:app --host 0.0.0.0 --port 8000
```

Variáveis do `.env`:

| Variável | Obrigatória | Descrição |
| --- | --- | --- |
| `GOOGLE_API_KEY` | sim | Chave do Google AI Studio. |
| `AURORA_MODEL` | não | Modelo Gemini dos agentes (padrão `gemini-2.5-flash`). |
| `AURORA_DATA_DIR` | não | Pasta dos bancos SQLite (padrão `.data`). |

- **Subir a API:** `uv run uvicorn aurora.api:app --host 0.0.0.0 --port 8000`. Nenhum serviço externo é necessário.
- **Restaurar os dados:** `uv run python -m aurora.restaurar`. Volta reservas e visitantes ao estado de `dados/` e também apaga as sessões. Rode com a API parada.
- Os arquivos de `dados/` nunca são alterados; o banco em `.data/` é criado na primeira subida a partir deles.
