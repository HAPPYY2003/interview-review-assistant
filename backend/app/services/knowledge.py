from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

try:
    import jieba
except ImportError:  # pragma: no cover - fallback keeps fixture mode usable
    jieba = None

try:
    from rank_bm25 import BM25Okapi
except ImportError:  # pragma: no cover
    BM25Okapi = None


@dataclass
class KnowledgeHit:
    id: str
    title: str
    text: str
    source: str
    score: float
    confidence: float


def tokenize(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text.lower()).strip()
    if jieba:
        return [token.strip() for token in jieba.lcut(normalized) if token.strip() and not token.isspace()]
    return re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", normalized)


class KnowledgeBase:
    def __init__(self, directory: Path):
        self.directory = directory
        self.documents = self._load_documents()
        self.corpus = [tokenize(item["text"]) for item in self.documents]
        self.index = BM25Okapi(self.corpus) if BM25Okapi and self.corpus else None

    def _load_documents(self) -> list[dict[str, str]]:
        documents: list[dict[str, str]] = []
        if not self.directory.exists():
            return documents
        for path in sorted(self.directory.glob("*.md")):
            content = path.read_text(encoding="utf-8")
            chunks = [chunk.strip() for chunk in re.split(r"(?=^##?\s+)", content, flags=re.MULTILINE) if chunk.strip()]
            for index, chunk in enumerate(chunks, 1):
                first_line = chunk.splitlines()[0].lstrip("# ").strip()
                documents.append({"id": f"kb-{path.stem}-{index}", "title": first_line or path.stem, "text": chunk, "source": path.name})
        return documents

    def search(self, query: str, limit: int = 3) -> list[KnowledgeHit]:
        if not query.strip() or not self.documents:
            return []
        query_tokens = tokenize(query)
        if not query_tokens:
            return []
        if self.index:
            raw_scores = list(self.index.get_scores(query_tokens))
        else:
            query_set = set(query_tokens)
            raw_scores = [sum(1 for token in tokens if token in query_set) / math.sqrt(max(1, len(tokens))) for tokens in self.corpus]
        ranked = sorted(enumerate(raw_scores), key=lambda pair: pair[1], reverse=True)[:limit]
        top = max((float(score) for _, score in ranked), default=0.0)
        hits: list[KnowledgeHit] = []
        for index, score in ranked:
            if float(score) <= 0:
                continue
            document = self.documents[index]
            query_coverage = len(set(query_tokens) & set(self.corpus[index])) / max(1, len(set(query_tokens)))
            relative = float(score) / top if top else 0.0
            confidence = round(min(1.0, 0.65 * relative + 0.35 * query_coverage), 3)
            hits.append(KnowledgeHit(**document, score=round(float(score), 3), confidence=confidence))
        return hits

