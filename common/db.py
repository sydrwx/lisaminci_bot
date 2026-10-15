"""
common/db.py — общее подключение к ChromaDB для bot, updater и parsers/wiki_xml.py

Если задан CHROMA_HOST — подключение к Chroma-серверу (docker-compose),
иначе — локальная база в ./chroma_db.

Эмбеддинги — многоязычная модель через fastembed (ONNX, без torch):
стандартная модель Chroma (all-MiniLM-L6-v2) англоязычная и плохо ищет по-русски.
"""

import os
import time
import logging
from typing import Any

import chromadb
from chromadb.api.types import Documents, EmbeddingFunction, Embeddings
from chromadb.utils.embedding_functions import register_embedding_function

CHROMA_HOST = os.getenv("CHROMA_HOST")
CHROMA_PORT = int(os.getenv("CHROMA_PORT", "8000"))
CHROMA_DIR  = "./chroma_db"
COLLECTION    = os.getenv("CHROMA_COLLECTION", "genshin_ml")
TG_COLLECTION = os.getenv("TG_COLLECTION", "tainted_grail")  # настольная игра «Осквернённый Грааль»
STATE_COLLECTION = os.getenv("STATE_COLLECTION", "bot_state")  # служебное состояние, см. common/state.py
EMBED_MODEL = os.getenv("EMBED_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
EMBED_CACHE = os.path.expanduser(os.getenv("EMBED_CACHE", "~/.cache/fastembed"))

log = logging.getLogger(__name__)


@register_embedding_function
class FastEmbedFunction(EmbeddingFunction[Documents]):
    def __init__(self, model_name: str = EMBED_MODEL, cache_dir: str = EMBED_CACHE):
        from fastembed import TextEmbedding
        self.model_name = model_name
        self.cache_dir = cache_dir
        self._model = TextEmbedding(model_name, cache_dir=cache_dir)

    def __call__(self, input: Documents) -> Embeddings:
        return [e for e in self._model.embed(list(input))]

    @staticmethod
    def name() -> str:
        return "fastembed"

    def get_config(self) -> dict[str, Any]:
        return {"model_name": self.model_name}

    @staticmethod
    def build_from_config(config: dict[str, Any]) -> "FastEmbedFunction":
        return FastEmbedFunction(config["model_name"])


_ef: FastEmbedFunction | None = None


def get_embedding_function() -> FastEmbedFunction:
    global _ef
    if _ef is None:
        log.info(f"Загружаю модель эмбеддингов {EMBED_MODEL}...")
        _ef = FastEmbedFunction()
    return _ef


def get_client(wait_seconds: int = 60):
    """Подключиться к ChromaDB; сервер при старте compose может быть ещё не готов."""
    if not CHROMA_HOST:
        return chromadb.PersistentClient(path=CHROMA_DIR)

    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
            client.heartbeat()
            return client
        except Exception as e:
            if time.monotonic() > deadline:
                raise
            log.info(f"Жду ChromaDB на {CHROMA_HOST}:{CHROMA_PORT}... ({e})")
            time.sleep(2)


def get_collection(client=None, create: bool = False, name: str = COLLECTION):
    client = client or get_client()
    ef = get_embedding_function()
    if create:
        return client.get_or_create_collection(
            name=name,
            embedding_function=ef,
            metadata={"hnsw:space": "cosine"},
        )
    return client.get_collection(name=name, embedding_function=ef)
