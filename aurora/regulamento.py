"""Consulta ao regulamento por capítulo e artigo, sem carregá-lo no contexto."""

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

from . import config

STOPWORDS = {
    "que", "para", "com", "por", "uma", "uns", "umas", "dos", "das", "nos", "nas",
    "como", "qual", "quais", "quando", "onde", "pode", "posso", "podem", "tem",
    "ter", "ser", "sao", "esta", "esse", "essa", "isso", "mais", "muito", "sobre",
    "ate", "aos", "pelo", "pela", "seu", "sua", "meu", "minha", "foi", "era", "hora",
    "horas", "regra", "regras", "regulamento", "condominio", "morador", "moradores",
}
MAX_ARTIGOS = 3
MAX_CHARS_ARTIGO = 1800
# Palavras comuns dos moradores que o regulamento escreve de outra forma.
SINONIMOS = {
    "cachorro": "animal caes",
    "cachorros": "animal caes",
    "gato": "animal",
    "gatos": "animal",
    "pet": "animal",
    "pets": "animal",
    "bicho": "animal",
    "barulho": "silencio",
    "carro": "veiculo garagem",
    "carros": "veiculo garagem",
    "estacionar": "garagem veiculo",
    "estaciono": "garagem veiculo",
    "moto": "garagem veiculo",
    "reciclagem": "lixo",
}


@dataclass(frozen=True)
class Artigo:
    texto: str
    tokens: frozenset[str]


@dataclass(frozen=True)
class Capitulo:
    titulo: str
    tokens_titulo: frozenset[str]
    tokens: frozenset[str]
    artigos: tuple[Artigo, ...]


def _sem_acento(texto: str) -> str:
    decomposto = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in decomposto if not unicodedata.combining(c))


def _palavras(texto: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", _sem_acento(texto))


def _tokens(texto: str) -> frozenset[str]:
    resultado = set()
    for p in _palavras(texto):
        if len(p) < 3 or p in STOPWORDS:
            continue
        resultado.add(p[:-1] if p.endswith("s") and len(p) > 4 else p)
    return frozenset(resultado)


@lru_cache(maxsize=1)
def _capitulos() -> tuple[Capitulo, ...]:
    texto = (config.DADOS_DIR / "regulamento.md").read_text(encoding="utf-8")
    capitulos = []
    for bloco in re.split(r"^## ", texto, flags=re.MULTILINE)[1:]:
        titulo, _, corpo = bloco.partition("\n")
        artigos = [
            a.strip()
            for a in re.split(r"^(?=\*\*Art\.)", corpo, flags=re.MULTILINE)
            if a.strip().startswith("**Art.")
        ]
        capitulos.append(
            Capitulo(
                titulo=titulo.strip(),
                tokens_titulo=_tokens(titulo),
                tokens=_tokens(corpo),
                artigos=tuple(Artigo(a, _tokens(a)) for a in artigos),
            )
        )
    return tuple(capitulos)


def consultar(pergunta: str) -> dict:
    """Devolve só os artigos mais relevantes do capítulo mais adequado à pergunta."""
    consulta = _tokens(pergunta)
    for palavra in _palavras(pergunta):
        consulta |= _tokens(SINONIMOS.get(palavra, ""))
    if not consulta:
        return {"encontrado": False}

    melhor, melhor_pontos = None, 0
    for cap in _capitulos():
        pontos = 5 * len(consulta & cap.tokens_titulo) + len(consulta & cap.tokens)
        if pontos > melhor_pontos:
            melhor, melhor_pontos = cap, pontos
    if melhor is None:
        return {"encontrado": False}

    ranqueados = sorted(
        melhor.artigos, key=lambda a: len(consulta & a.tokens), reverse=True
    )
    trechos = [
        a.texto[:MAX_CHARS_ARTIGO]
        for a in ranqueados[:MAX_ARTIGOS]
        if consulta & a.tokens
    ]
    if not trechos:
        return {"encontrado": False}
    return {"encontrado": True, "capitulo": melhor.titulo, "trechos": trechos}
