import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct

sys.path.insert(0, str(Path(__file__).parent.parent))
import config
from agent.sop_loader import load_sop_file

logger = logging.getLogger(__name__)

COLLECTION_NAME = "sop_entry_cases"

_qdrant: QdrantClient | None = None
_embed_fn: Callable[[list[str]], list[list[float]]] | None = None


@dataclass
class SearchResult:
    sop_file: str
    case_id: str
    scenario: str
    title: str
    keywords: list[str] = field(default_factory=list)
    score: float = 0.0


# ── 內部初始化 ──────────────────────────────────────────────────────────────────

def _get_qdrant() -> QdrantClient:
    global _qdrant
    if _qdrant is None:
        _qdrant = QdrantClient(host=config.QDRANT_HOST, port=config.QDRANT_PORT)
    return _qdrant


def _get_embed_fn() -> Callable[[list[str]], list[list[float]]]:
    global _embed_fn
    if _embed_fn is None:
        if config.EMBEDDING_BASE_URL is None:
            # 本地 sentence-transformers
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer(config.EMBEDDING_MODEL)

            def _local_embed(texts: list[str]) -> list[list[float]]:
                return model.encode(texts, normalize_embeddings=True).tolist()

            _embed_fn = _local_embed
        else:
            # 內部 embedding API（OpenAI 相容）
            from openai import OpenAI
            client = OpenAI(
                base_url=config.EMBEDDING_BASE_URL,
                api_key=config.LLM_API_KEY,
            )

            def _remote_embed(texts: list[str]) -> list[list[float]]:
                resp = client.embeddings.create(
                    model=config.EMBEDDING_MODEL, input=texts
                )
                return [d.embedding for d in resp.data]

            _embed_fn = _remote_embed

    return _embed_fn


def _embed(texts: list[str]) -> list[list[float]]:
    return _get_embed_fn()(texts)


# ── 公開 API ───────────────────────────────────────────────────────────────────

def index_all_sops(sop_dir: str) -> int:
    """掃描 sop_dir 下所有 .md，索引每個 case（所有 case 都是潛在入口）。

    回傳成功索引的 case 數量。
    """
    sop_path = Path(sop_dir)
    points: list[PointStruct] = []

    for md_file in sorted(sop_path.glob("*.md")):
        if md_file.name.startswith("_"):  # 跳過 _index.md 等輔助檔案
            continue

        sop_data = load_sop_file(str(md_file))
        scenario = sop_data["metadata"].get("scenario", "")

        for case_id, case in sop_data["cases"].items():
            keywords: list[str] = case.get("keywords", [])
            # 搜尋文字 = 標題 + symptom + keywords（多語言混合）
            text = " ".join(
                filter(None, [case.get("title", ""), case["symptom"]] + keywords)
            )

            vector = _embed([text])[0]
            points.append(
                PointStruct(
                    id=len(points),
                    vector=vector,
                    payload={
                        "sop_file": sop_data["sop_file"],
                        "case_id": case_id,
                        "scenario": scenario,
                        "title": case.get("title", ""),
                        "keywords": keywords,
                    },
                )
            )

    if not points:
        logger.warning("index_all_sops: no cases found in '%s'", sop_dir)
        return 0

    qdrant = _get_qdrant()
    vector_size = len(points[0].vector)

    # 重建 collection（冪等）
    existing = {c.name for c in qdrant.get_collections().collections}
    if COLLECTION_NAME in existing:
        qdrant.delete_collection(COLLECTION_NAME)

    qdrant.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
    )
    qdrant.upsert(collection_name=COLLECTION_NAME, points=points)

    logger.info("Indexed %d entry case(s) into '%s'", len(points), COLLECTION_NAME)
    return len(points)


def search_entry_cases(query: str, top_k: int = 1) -> list[SearchResult]:
    """搜尋最相近的入口 case，回傳帶有 score 的結果清單。"""
    qdrant = _get_qdrant()
    vector = _embed([query])[0]

    response = qdrant.query_points(
        collection_name=COLLECTION_NAME,
        query=vector,
        limit=top_k,
    )

    return [
        SearchResult(
            sop_file=h.payload["sop_file"],
            case_id=h.payload["case_id"],
            scenario=h.payload["scenario"],
            title=h.payload["title"],
            keywords=h.payload.get("keywords", []),
            score=h.score,
        )
        for h in response.points
    ]
