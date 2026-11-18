import os
from pathlib import Path

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent.parent
load_dotenv(RAIZ / ".env")

# Usa a chave do Google AI Studio (GOOGLE_API_KEY), não o Vertex AI.
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "FALSE")

DADOS_DIR = RAIZ / "dados"
DATA_DIR = Path(os.environ.get("AURORA_DATA_DIR") or RAIZ / ".data")
DB_CONDOMINIO = DATA_DIR / "condominio.db"
DB_SESSOES = DATA_DIR / "sessoes.db"

MODELO = os.environ.get("AURORA_MODEL") or "gemini-2.5-flash"
APP_NAME = "aurora"
# Todas as sessões usam o mesmo user_id; o apartamento vive no state da sessão.
USER_ID = "morador"
