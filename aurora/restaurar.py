"""Restaura reservas e visitantes ao estado de `dados/` e apaga as sessões.

Rode com a API parada: `uv run python -m aurora.restaurar`.
"""

from . import armazenamento, config


def main() -> None:
    armazenamento.restaurar()
    for sufixo in ("", "-wal", "-shm"):
        arquivo = config.DB_SESSOES.with_name(config.DB_SESSOES.name + sufixo)
        arquivo.unlink(missing_ok=True)
    print("Dados restaurados: reservas e visitantes iniciais; sessões apagadas.")


if __name__ == "__main__":
    main()
