"""Снапшот векторов базы знаний: считается заранее, студенту не нужен.

    python -m kb.build_snapshot     # data/kb/*.md + сервер эмбеддингов → kb/vectors.json

Эмбеддинги считаются один раз при сборке эталона и складываются в
kb/vectors.json. `make seed` грузит статьи вместе с векторами в
kb_chunks — сервер эмбеддингов студенту не требуется. Пересчёт нужен,
только если вы меняете статьи базы знаний: поднимите любой
OpenAI-совместимый сервер эмбеддингов и задайте EMBED_* в .env
(см. .env.example).

Размерность векторов зашита в схеме БД — vector(768): модель с другой
размерностью потребует правки схемы и пересборки базы, в рамках проекта
этого делать не нужно.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

try:
    import httpx2 as httpx
except ImportError:  # pragma: no cover
    import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

KB_DIR = ROOT / "data" / "kb"
OUT_FILE = Path(__file__).resolve().parent / "vectors.json"

# Сервер эмбеддингов из .env; значения по умолчанию — конфигурация,
# которой собран приложенный kb/vectors.json.
EMBED_MODEL = os.environ.get("EMBED_MODEL", "text-embedding-embeddinggemma-300m-qat")
EMBED_URL = os.environ.get("EMBED_URL", "http://127.0.0.1:1234/v1/embeddings")

# Префиксы — контракт модели эмбеддингов: разные для запроса и документа.
QUERY_PREFIX = os.environ.get("EMBED_QUERY_PREFIX", "task: search result | query: ")
DOC_PREFIX = os.environ.get("EMBED_DOC_PREFIX", "title: none | text: ")


def read_articles() -> list[dict]:
    articles = []
    for md in sorted(KB_DIR.glob("*.md")):
        lines = md.read_text(encoding="utf-8").splitlines()
        title = lines[0].lstrip("# ").strip() if lines else md.stem
        body = "\n".join(lines[1:]).strip()
        articles.append({"source": md.stem, "title": title, "body": body})
    return articles


def embed(texts: list[str]) -> list[list[float]]:
    with httpx.Client(trust_env=False, timeout=120) as client:
        r = client.post(
            EMBED_URL,
            json={"model": EMBED_MODEL, "input": texts},
        )
        r.raise_for_status()
        items = sorted(r.json()["data"], key=lambda d: d["index"])
        return [item["embedding"] for item in items]


def main() -> None:
    articles = read_articles()
    vectors = embed([DOC_PREFIX + a["title"] + "\n" + a["body"] for a in articles])
    dim = len(vectors[0])
    OUT_FILE.write_text(
        json.dumps(
            [{**a, "vector": v} for a, v in zip(articles, vectors)],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"ok: {len(articles)} статей, dim={dim} → {OUT_FILE.name}")


if __name__ == "__main__":
    main()
