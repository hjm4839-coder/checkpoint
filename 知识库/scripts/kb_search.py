#!/usr/bin/env python3
"""
kb_search.py — Obsidian 知识库混合检索引擎

本地关键词检索 + 向量语义召回，SQLite 存储，不依赖外部服务。

用法:
  python3 kb_search.py index --incremental      # 增量索引
  python3 kb_search.py search "<query>" --top-k 5  # 混合搜索
  python3 kb_search.py rebuild                   # 重建全部索引
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import struct
import sys
import textwrap
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# 配置常量
# ---------------------------------------------------------------------------

OBSIDIAN_VAULT = Path(os.environ.get("OBSIDIAN_VAULT", os.path.expanduser("~/obsidian/知识库")))
SOLUTION_DIR = OBSIDIAN_VAULT / "Claude方案"
CACHE_DIR = Path(os.environ.get("KB_CACHE_DIR", os.path.expanduser("~/Library/Caches/claude-obsidian-semantic")))
DB_PATH = CACHE_DIR / "kb_index.db"
MODEL_CACHE_DIR = CACHE_DIR / "models"
CHUNK_MIN = 800
CHUNK_MAX = 1200
CHUNK_OVERLAP = 150
TOP_K_DEFAULT = 5
E5_MODEL_NAME = "intfloat/multilingual-e5-small"
E5_QUERY_PREFIX = "query: "
E5_PASSAGE_PREFIX = "passage: "
EMBEDDING_DIM = 384  # multilingual-e5-small


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def safe_json_list(raw: Any) -> list[str]:
    """将 frontmatter 中的单值/列表/JSON 字符串统一转为字符串列表。"""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(v).strip() for v in raw if v]
    if isinstance(raw, str):
        s = raw.strip()
        if s.startswith("[") and s.endswith("]"):
            try:
                items = json.loads(s)
                if isinstance(items, list):
                    return [str(v).strip() for v in items if v]
            except (json.JSONDecodeError, TypeError):
                pass
        return [s]
    return [str(raw).strip()]


def wikilink_from_path(path: Path) -> str:
    """从 Markdown 路径生成 Obsidian wikilink。"""
    rel = path.relative_to(OBSIDIAN_VAULT) if path.is_relative_to(OBSIDIAN_VAULT) else path
    name = rel.with_suffix("").as_posix()
    return f"[[{name}]]"


# ---------------------------------------------------------------------------
# Frontmatter 解析
# ---------------------------------------------------------------------------

def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """解析 YAML frontmatter，返回 (元数据字典, 剩余正文)。

    使用简单的逐行解析而非 PyYAML，避免额外依赖。
    支持的格式：
      key: value
      key: [a, b, c]
      key:
        - a
        - b
    """
    meta: dict[str, Any] = {}
    body = text
    if not text.startswith("---"):
        return meta, body

    end_idx = text.find("---", 3)
    if end_idx == -1:
        return meta, body

    fm_text = text[3:end_idx].strip()
    body = text[end_idx + 3:].strip()

    current_key: Optional[str] = None
    current_list: list[str] = []

    for line in fm_text.split("\n"):
        # 列表项续行
        list_match = re.match(r"^\s+-\s+(.+)", line)
        if list_match and current_key:
            current_list.append(list_match.group(1).strip())
            continue

        # 刷新上一个累积的 key
        if current_key is not None:
            meta[current_key] = current_list if len(current_list) > 1 else (current_list[0] if current_list else "")
            current_key = None
            current_list = []

        # 新的 key: value 或 key: [array]
        kv_match = re.match(r"^(\w[\w-]*):\s*(.*)", line)
        if kv_match:
            key = kv_match.group(1)
            val = kv_match.group(2).strip()
            if val.startswith("[") and val.endswith("]"):
                inner = val[1:-1].strip()
                items = []
                for item in inner.split(","):
                    item = item.strip().strip('"').strip("'")
                    if item:
                        items.append(item)
                meta[key] = items
            elif val == "":
                current_key = key
                current_list = []
            else:
                meta[key] = val.strip('"').strip("'")

    # 处理最后一个 key
    if current_key is not None:
        meta[current_key] = current_list if len(current_list) > 1 else (current_list[0] if current_list else "")

    return meta, body


def extract_title(text: str) -> Optional[str]:
    """提取正文第一个 H1 标题。"""
    m = re.match(r"^#\s+(.+)$", text, re.MULTILINE)
    return m.group(1).strip() if m else None


# ---------------------------------------------------------------------------
# 文档切块
# ---------------------------------------------------------------------------

@dataclass
class Chunk:
    """一个文本块，包含标题、章节信息和正文。"""
    heading: Optional[str]           # 最近的 ##/### 标题
    content: str                     # 文本内容
    ordinal: int                     # 块序号
    section_title: Optional[str] = None  # H1 标题


def chunk_markdown(body: str, h1_title: Optional[str]) -> list[Chunk]:
    """按 ##/### 标题切分正文，过长的章节再按段落拆分。

    每块 800-1200 字符，相邻块重叠约 150 字符。
    """
    # 按 ## 或 ### 标题分割
    sections = re.split(r"(?=^#{2,3}\s)", body, flags=re.MULTILINE)
    chunks: list[Chunk] = []
    ordinal = 0

    for section in sections:
        section = section.strip()
        if not section:
            continue

        # 提取当前章节标题
        heading_match = re.match(r"^#{2,3}\s+(.*)", section)
        heading = heading_match.group(1).strip() if heading_match else ""
        content = section[heading_match.end():].strip() if heading_match else section

        # 将过长的章节按段落拆分
        paragraphs = _split_paragraphs(content)
        sub_ordinal = 0

        for para_group in _chunk_paragraphs(paragraphs):
            chunk = Chunk(
                heading=heading,
                content=para_group,
                ordinal=ordinal,
                section_title=h1_title,
            )
            chunks.append(chunk)
            ordinal += 1
            sub_ordinal += 1

    return chunks


def _split_paragraphs(text: str) -> list[str]:
    """按空行/换行拆分段落，过滤空段。长段落进一步按句子拆分。"""
    parts = re.split(r"\n{2,}", text)
    result = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if len(part) > CHUNK_MAX:
            # 长段落按句子边界拆分
            result.extend(_split_long_paragraph(part))
        else:
            result.append(part)
    return result


def _split_long_paragraph(text: str) -> list[str]:
    """将超长段落按句子边界拆分为适合大小的片段。"""
    # 按句号、换行等切分
    sentences = re.split(r"(?<=[。！？\.\!\?\n])", text)
    chunks = []
    current = []
    current_len = 0
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        if current_len + len(s) > CHUNK_MAX and current:
            chunks.append(" ".join(current))
            current = []
            current_len = 0
        current.append(s)
        current_len += len(s)
    if current:
        chunks.append(" ".join(current))
    return chunks if chunks else [text]


def _chunk_paragraphs(paragraphs: list[str]) -> list[str]:
    """将段落组合成 800-1200 字符的块，带重叠。"""
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for para in paragraphs:
        para_len = len(para)
        if current_len + para_len > CHUNK_MAX and current:
            # 当前块已满
            chunks.append("\n\n".join(current))
            # 保留末尾段落做重叠
            overlap_len = 0
            overlap: list[str] = []
            for p in reversed(current):
                if overlap_len + len(p) <= CHUNK_OVERLAP:
                    overlap.insert(0, p)
                    overlap_len += len(p)
                else:
                    break
            current = overlap
            current_len = overlap_len

        current.append(para)
        current_len += para_len

    if current:
        chunks.append("\n\n".join(current))

    return chunks


# ---------------------------------------------------------------------------
# Embedding 适配器
# ---------------------------------------------------------------------------

class EmbedderError(Exception):
    """Embedding 模型不可用时抛出。"""
    pass


class BaseEmbedder:
    """Embedder 抽象基类。"""

    def embed_passages(self, passages: list[str]) -> list[list[float]]:
        raise NotImplementedError

    def embed_queries(self, queries: list[str]) -> list[list[float]]:
        raise NotImplementedError

    @property
    def dimension(self) -> int:
        raise NotImplementedError

    @property
    def model_name(self) -> str:
        raise NotImplementedError


class FakeEmbedder(BaseEmbedder):
    """测试用假 Embedder：基于文本长度生成确定性伪向量。

    不可用于任何语义匹配评测。
    """

    DIM = 16

    def embed_passages(self, passages: list[str]) -> list[list[float]]:
        return [self._pseudo_vector(p, prefix="passage: ") for p in passages]

    def embed_queries(self, queries: list[str]) -> list[list[float]]:
        return [self._pseudo_vector(q, prefix="query: ") for q in queries]

    def _pseudo_vector(self, text: str, prefix: str) -> list[float]:
        full = (prefix + text).encode("utf-8")
        h = hashlib.sha256(full).digest()
        vec = []
        for i in range(self.DIM):
            # 从 hash 中取 4 字节组成 float32
            idx = (i * 4) % len(h)
            raw = struct.unpack("<f", h[idx:idx + 4])[0]
            vec.append(round(raw, 6))
        # L2 归一化
        norm = sum(v * v for v in vec) ** 0.5
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec

    @property
    def dimension(self) -> int:
        return self.DIM

    @property
    def model_name(self) -> str:
        return "fake"


class E5Embedder(BaseEmbedder):
    """multilingual-e5-small 本地 embedding 模型。"""

    def __init__(self, model_name: str = E5_MODEL_NAME, cache_dir: Optional[Path] = None):
        self._model_name = model_name
        self._cache_dir = str(cache_dir) if cache_dir else None
        self._model: Any = None

    def _load_model(self) -> None:
        if self._model is not None:
            return
        try:
            import os
            # 国内优先使用 HF 镜像，避免下载超时（用户可通过 HF_ENDPOINT 覆盖）
            if "HF_ENDPOINT" not in os.environ:
                os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(
                self._model_name,
                cache_folder=self._cache_dir,
            )
        except ImportError:
            raise EmbedderError(
                "sentence-transformers 未安装。请运行: pip3 install sentence-transformers"
            )
        except Exception as e:
            raise EmbedderError(f"无法加载 embedding 模型 {self._model_name}: {e}")

    def embed_passages(self, passages: list[str]) -> list[list[float]]:
        self._load_model()
        texts = [E5_PASSAGE_PREFIX + p for p in passages]
        embeddings = self._model.encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return [vec.tolist() for vec in embeddings]

    def embed_queries(self, queries: list[str]) -> list[list[float]]:
        self._load_model()
        texts = [E5_QUERY_PREFIX + q for q in queries]
        embeddings = self._model.encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return [vec.tolist() for vec in embeddings]

    @property
    def dimension(self) -> int:
        return EMBEDDING_DIM

    @property
    def model_name(self) -> str:
        return self._model_name


def create_embedder(fake: bool = False, cache_dir: Optional[Path] = None) -> BaseEmbedder:
    """工厂：创建 embedder，失败则抛 EmbedderError。"""
    if fake:
        return FakeEmbedder()
    return E5Embedder(cache_dir=cache_dir)


def try_create_embedder(cache_dir: Optional[Path] = None) -> Optional[BaseEmbedder]:
    """尝试创建 embedder，失败返回 None。"""
    try:
        return create_embedder(fake=False, cache_dir=cache_dir)
    except EmbedderError:
        return None


# ---------------------------------------------------------------------------
# SQLite 索引管理
# ---------------------------------------------------------------------------

def _vector_to_blob(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _blob_to_vector(blob: bytes) -> list[float]:
    count = len(blob) // 4
    return list(struct.unpack(f"{count}f", blob))


def _dot_product(a: list[float], b: list[float]) -> float:
    """归一化向量的点积 = 余弦相似度。"""
    return sum(x * y for x, y in zip(a, b))


@dataclass
class ChunkRecord:
    id: int
    path: str
    ordinal: int
    heading: Optional[str]
    content: str
    embedding: list[float]


class IndexManager:
    """SQLite 索引管理：创建、增量更新、查询。"""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None

    def __enter__(self):
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()
        return self

    def __exit__(self, *args):
        if self.conn:
            self.conn.close()
            self.conn = None

    def _init_schema(self) -> None:
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS documents (
                path TEXT PRIMARY KEY,
                sha256 TEXT NOT NULL,
                mtime_ns INTEGER NOT NULL,
                title TEXT,
                aliases_json TEXT DEFAULT '[]',
                keywords_json TEXT DEFAULT '[]',
                tags_json TEXT DEFAULT '[]',
                project TEXT
            );

            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                heading TEXT,
                content TEXT NOT NULL,
                embedding BLOB NOT NULL,
                dimension INTEGER NOT NULL,
                FOREIGN KEY(path) REFERENCES documents(path) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(path);
            CREATE INDEX IF NOT EXISTS idx_documents_sha256 ON documents(sha256);
        """)

    # ---- 文档操作 ----

    def upsert_document(self, path: str, meta: dict[str, Any], content_hash: str, mtime_ns: int) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO documents (path, sha256, mtime_ns, title, aliases_json, keywords_json, tags_json, project)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                path,
                content_hash,
                mtime_ns,
                meta.get("title"),
                json.dumps(safe_json_list(meta.get("aliases")), ensure_ascii=False),
                json.dumps(safe_json_list(meta.get("keywords")), ensure_ascii=False),
                json.dumps(safe_json_list(meta.get("tags")), ensure_ascii=False),
                meta.get("project"),
            ),
        )

    def get_document(self, path: str) -> Optional[dict[str, Any]]:
        row = self.conn.execute("SELECT * FROM documents WHERE path = ?", (path,)).fetchone()
        if not row:
            return None
        cols = [c[0] for c in self.conn.execute("SELECT * FROM documents LIMIT 0").description]
        return dict(zip(cols, row))

    def delete_document(self, path: str) -> None:
        self.conn.execute("DELETE FROM documents WHERE path = ?", (path,))

    def all_document_paths(self) -> set[str]:
        rows = self.conn.execute("SELECT path FROM documents").fetchall()
        return {r[0] for r in rows}

    # ---- 块操作 ----

    def insert_chunk(self, path: str, chunk: Chunk, embedding: list[float]) -> int:
        cursor = self.conn.execute(
            """INSERT INTO chunks (path, ordinal, heading, content, embedding, dimension)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                path,
                chunk.ordinal,
                chunk.heading,
                chunk.content,
                _vector_to_blob(embedding),
                len(embedding),
            ),
        )
        return cursor.lastrowid

    def delete_chunks(self, path: str) -> None:
        self.conn.execute("DELETE FROM chunks WHERE path = ?", (path,))

    def get_chunks(self, path: str) -> list[ChunkRecord]:
        rows = self.conn.execute(
            "SELECT id, path, ordinal, heading, content, embedding FROM chunks WHERE path = ? ORDER BY ordinal",
            (path,),
        ).fetchall()
        return [
            ChunkRecord(
                id=r[0],
                path=r[1],
                ordinal=r[2],
                heading=r[3],
                content=r[4],
                embedding=_blob_to_vector(r[5]),
            )
            for r in rows
        ]

    def all_chunks_with_embeddings(self) -> list[ChunkRecord]:
        """获取所有块的向量（用于暴力搜索）。"""
        rows = self.conn.execute(
            "SELECT id, path, ordinal, heading, content, embedding FROM chunks ORDER BY id"
        ).fetchall()
        return [
            ChunkRecord(
                id=r[0],
                path=r[1],
                ordinal=r[2],
                heading=r[3],
                content=r[4],
                embedding=_blob_to_vector(r[5]),
            )
            for r in rows
        ]

    def get_document_meta(self, path: str) -> Optional[dict[str, Any]]:
        row = self.conn.execute(
            "SELECT aliases_json, keywords_json, tags_json, project, title FROM documents WHERE path = ?",
            (path,),
        ).fetchone()
        if not row:
            return None
        return {
            "aliases": json.loads(row[0]) if row[0] else [],
            "keywords": json.loads(row[1]) if row[1] else [],
            "tags": json.loads(row[2]) if row[2] else [],
            "project": row[3],
            "title": row[4],
        }

    def all_document_metas(self) -> dict[str, dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT path, aliases_json, keywords_json, tags_json, project, title FROM documents"
        ).fetchall()
        result = {}
        for r in rows:
            result[r[0]] = {
                "aliases": json.loads(r[1]) if r[1] else [],
                "keywords": json.loads(r[2]) if r[2] else [],
                "tags": json.loads(r[3]) if r[3] else [],
                "project": r[4],
                "title": r[5],
            }
        return result


# ---------------------------------------------------------------------------
# 词法检索
# ---------------------------------------------------------------------------

@dataclass
class LexicalHit:
    path: str
    bucket: int            # 0=aliases, 1=keywords, 2=tags, 3=body(标题/正文)
    matched_terms: list[str]
    matched_in: str        # 匹配到的字段/片段
    heading: Optional[str] = None
    snippet: Optional[str] = None


def lexical_search(query: str, index: IndexManager, vault_dir: Path = SOLUTION_DIR) -> list[LexicalHit]:
    """aliases > keywords > tags > 标题/正文 的多关键词匹配。

    将 query 拆为多个关键词，每个都要求匹配才记为命中。
    """
    keywords = _extract_keywords(query)
    if not keywords:
        return []

    hits: list[LexicalHit] = []
    metas = index.all_document_metas()

    for path, meta in metas.items():
        # Bucket 0: aliases（任意关键词命中即算）
        alias_matches = _match_any(keywords, meta.get("aliases", []))
        if alias_matches:
            hits.append(LexicalHit(path, 0, alias_matches, "aliases"))
            continue

        # Bucket 1: keywords
        kw_matches = _match_any(keywords, meta.get("keywords", []))
        if kw_matches:
            hits.append(LexicalHit(path, 1, kw_matches, "keywords"))
            continue

        # Bucket 2: tags
        tag_matches = _match_any(keywords, meta.get("tags", []))
        if tag_matches:
            hits.append(LexicalHit(path, 2, tag_matches, "tags"))
            continue

        # Bucket 3: 标题匹配（优先于正文）
        title = meta.get("title") or ""
        title_matches = _match_any(keywords, [title])
        if title_matches:
            hits.append(LexicalHit(path, 3, title_matches, "title", heading=title))
            continue

    # Bucket 3 (正文): 直接 grep 源文件（不在 SQLite 存全文）
    for path in metas:
        # 只在还没有词法命中的文件上做正文搜索
        if any(h.path == path for h in hits):
            continue
        full_path = vault_dir / path if not path.startswith(str(vault_dir)) else Path(path)
        try:
            text = full_path.read_text(encoding="utf-8")
            body_matches = _match_any(keywords, [text])
            if body_matches:
                snippet = _extract_snippet(text, keywords)
                hits.append(LexicalHit(path, 3, body_matches, "body", heading=None, snippet=snippet))
        except (OSError, UnicodeDecodeError):
            continue

    return hits


def _extract_keywords(query: str) -> list[str]:
    """从查询中提取有意义的关键词（排除停用词）。"""
    stopwords = {"的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都", "一",
                 "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着", "没有",
                 "看", "好", "自己", "这", "他", "她", "它", "们", "那", "些", "什么", "怎么",
                 "如何", "哪个", "为什么", "可以", "这个", "那个"}
    # 中英文混合分词
    tokens: list[str] = []
    # 中文按字组合（2-4 字短语）
    chinese_chars = re.findall(r"[一-鿿]+", query)
    for segment in chinese_chars:
        if len(segment) <= 1:
            continue
        if segment not in stopwords:
            tokens.append(segment)
    # 英文/数字词
    english_words = re.findall(r"[a-zA-Z0-9]+", query)
    for w in english_words:
        if w.lower() not in {"a", "an", "the", "is", "are", "was", "were", "in", "on", "at", "to", "for", "of", "and", "or", "not"}:
            tokens.append(w)
    return tokens


def _match_any(keywords: list[str], fields: list[str]) -> list[str]:
    """返回在 fields 中匹配到的关键词列表。"""
    matched = []
    for kw in keywords:
        kw_lower = kw.lower()
        for f in fields:
            if kw_lower in f.lower():
                matched.append(kw)
                break
    return matched


def _extract_snippet(text: str, keywords: list[str], context_chars: int = 120) -> Optional[str]:
    """提取第一个关键词匹配位置周围的文本片段。"""
    for kw in keywords:
        idx = text.lower().find(kw.lower())
        if idx != -1:
            start = max(0, idx - context_chars)
            end = min(len(text), idx + len(kw) + context_chars)
            snippet = text[start:end].strip()
            if start > 0:
                snippet = "…" + snippet
            if end < len(text):
                snippet = snippet + "…"
            return snippet
    return None


# ---------------------------------------------------------------------------
# 语义检索
# ---------------------------------------------------------------------------

@dataclass
class SemanticHit:
    path: str
    score: float
    chunk_id: int
    heading: Optional[str]
    snippet: str


def semantic_search(
    query: str,
    embedder: BaseEmbedder,
    index: IndexManager,
    top_k: int = TOP_K_DEFAULT,
) -> list[SemanticHit]:
    """向量相似度检索：对查询向量化，与所有 chunk 计算余弦相似度。"""
    try:
        query_vec = embedder.embed_queries([query])[0]
    except Exception:
        return []

    all_chunks = index.all_chunks_with_embeddings()
    if not all_chunks:
        return []

    # 计算所有 chunk 的相似度得分
    scored: list[tuple[float, ChunkRecord]] = []
    for chunk in all_chunks:
        score = _dot_product(query_vec, chunk.embedding)
        scored.append((score, chunk))

    # 按分数降序排列
    scored.sort(key=lambda x: x[0], reverse=True)

    # 按文件去重，每个文件只保留最高分 chunk
    seen_paths: set[str] = set()
    hits: list[SemanticHit] = []
    for score, chunk in scored:
        if chunk.path in seen_paths:
            continue
        seen_paths.add(chunk.path)
        if len(hits) >= top_k:
            break
        hits.append(SemanticHit(
            path=chunk.path,
            score=round(score, 4),
            chunk_id=chunk.id,
            heading=chunk.heading,
            snippet=_truncate_snippet(chunk.content, 300),
        ))

    return hits


def _truncate_snippet(text: str, max_len: int = 300) -> str:
    """截断文本为可读片段。"""
    if len(text) <= max_len:
        return text
    # 尽量在词、句边界截断
    truncated = text[:max_len]
    # 回退到最后一个句号或空行
    for sep in ["\n\n", "。", ". ", " ", "\n"]:
        last = truncated.rfind(sep)
        if last > max_len // 2:
            return truncated[:last + len(sep.rstrip())].strip() + "…"
    return truncated.strip() + "…"


# ---------------------------------------------------------------------------
# 混合排序
# ---------------------------------------------------------------------------

BUCKET_LABELS = {
    0: "aliases",
    1: "keywords",
    2: "tags",
    3: "title/body",
    4: "semantic-only",
}


@dataclass
class SearchResult:
    path: str
    wikilink: str
    bucket: int                # 0=aliases, 1=keywords, 2=tags, 3=body, 4=semantic-only
    bucket_label: str
    matched_terms: list[str]
    semantic_score: float
    heading: Optional[str]
    snippet: str
    title: Optional[str] = None
    project: Optional[str] = None


def hybrid_search(
    query: str,
    embedder: BaseEmbedder,
    index: IndexManager,
    top_k: int = TOP_K_DEFAULT,
    vault_dir: Path = SOLUTION_DIR,
) -> list[SearchResult]:
    """混合检索：词法匹配 + 语义召回，按优先级合并排序。"""
    # 1. 词法检索
    lexical_hits = lexical_search(query, index, vault_dir)
    lexical_paths = {h.path for h in lexical_hits}

    # 2. 语义检索（召回比 top_k 多一些，方便去重后合并）
    semantic_hits = semantic_search(query, embedder, index, top_k=max(10, top_k * 3))

    # 3. 合并
    results: dict[str, SearchResult] = {}
    metas = index.all_document_metas()

    for h in lexical_hits:
        meta = metas.get(h.path, {})
        results[h.path] = SearchResult(
            path=h.path,
            wikilink=wikilink_from_path(vault_dir / h.path),
            bucket=h.bucket,
            bucket_label=BUCKET_LABELS[h.bucket],
            matched_terms=h.matched_terms,
            semantic_score=1.0,  # 词法命中默认最高语义分
            heading=h.heading or meta.get("title"),
            snippet=h.snippet or "",
            title=meta.get("title"),
            project=meta.get("project"),
        )

    for s in semantic_hits:
        if s.path in lexical_paths:
            # 词法已命中：用语义分数做次级排序
            existing = results.get(s.path)
            if existing:
                existing.semantic_score = max(existing.semantic_score, s.score)
                if not existing.heading:
                    existing.heading = s.heading
                if not existing.snippet or len(existing.snippet) < 20:
                    existing.snippet = s.snippet
            continue

        # 纯语义命中
        meta = metas.get(s.path, {})
        results[s.path] = SearchResult(
            path=s.path,
            wikilink=wikilink_from_path(vault_dir / s.path),
            bucket=4,
            bucket_label=BUCKET_LABELS[4],
            matched_terms=[],
            semantic_score=s.score,
            heading=s.heading or meta.get("title"),
            snippet=s.snippet,
            title=meta.get("title"),
            project=meta.get("project"),
        )

    # 4. 排序：bucket 升序 → semantic_score 降序
    sorted_results = sorted(
        results.values(),
        key=lambda r: (r.bucket, -r.semantic_score),
    )

    return sorted_results[:top_k]


# ---------------------------------------------------------------------------
# 索引核心逻辑
# ---------------------------------------------------------------------------

def _scan_markdown_files(vault_dir: Path = SOLUTION_DIR) -> list[Path]:
    """扫描 Claude方案/ 下所有 .md 文件。"""
    if not vault_dir.exists():
        return []
    files: list[Path] = []
    for md in vault_dir.rglob("*.md"):
        if md.is_file():
            files.append(md)
    return files


def index_file(
    path: Path,
    embedder: BaseEmbedder,
    index: IndexManager,
    vault_dir: Path = SOLUTION_DIR,
) -> bool:
    """索引单个文件。返回 True 表示成功，False 表示跳过（内容未变化）。"""
    rel_path = path.relative_to(vault_dir).as_posix() if path.is_relative_to(vault_dir) else path.as_posix()

    try:
        raw_text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        print(f"  [WARN] 无法读取 {rel_path}: {e}", file=sys.stderr)
        return False

    content_hash = sha256(raw_text)
    mtime_ns = path.stat().st_mtime_ns

    # 检查是否需要更新
    existing = index.get_document(rel_path)
    if existing and existing["sha256"] == content_hash:
        return False  # 未变化，跳过

    # 删除旧块
    if existing:
        index.delete_chunks(rel_path)

    # 解析
    meta, body = parse_frontmatter(raw_text)
    title = meta.get("title") or extract_title(body)
    meta["title"] = title

    # 切块
    chunks = chunk_markdown(body, title)

    if not chunks:
        return False

    # 构建 passage 文本（含元数据前缀）
    passages: list[str] = []
    for chunk in chunks:
        parts = []
        if title:
            parts.append(f"标题: {title}")
        if meta.get("aliases"):
            parts.append(f"别名: {', '.join(safe_json_list(meta.get('aliases')))}")
        if meta.get("keywords"):
            parts.append(f"关键词: {', '.join(safe_json_list(meta.get('keywords')))}")
        if meta.get("tags"):
            parts.append(f"标签: {', '.join(safe_json_list(meta.get('tags')))}")
        if meta.get("project"):
            parts.append(f"项目: {meta['project']}")
        if chunk.heading:
            parts.append(f"章节: {chunk.heading}")
        parts.append(chunk.content)
        passages.append("\n".join(parts))

    # 生成向量
    try:
        embeddings = embedder.embed_passages(passages)
    except Exception as e:
        print(f"  [ERROR] embedding 失败 {rel_path}: {e}", file=sys.stderr)
        return False

    # 写入数据库
    index.upsert_document(rel_path, meta, content_hash, mtime_ns)
    for chunk, vec in zip(chunks, embeddings):
        index.insert_chunk(rel_path, chunk, vec)

    return True


def index_incremental(
    embedder: BaseEmbedder,
    index: IndexManager,
    vault_dir: Path = SOLUTION_DIR,
) -> dict[str, int]:
    """增量索引：新增/修改的文件重新索引，删除的文件清理。"""
    stats = {"new": 0, "updated": 0, "skipped": 0, "deleted": 0}

    # 扫描磁盘文件
    disk_files = _scan_markdown_files(vault_dir)
    disk_rel_paths = {
        p.relative_to(vault_dir).as_posix() if p.is_relative_to(vault_dir) else p.as_posix(): p
        for p in disk_files
    }

    # 数据库现有文件
    db_paths = index.all_document_paths()

    # 删除已不存在的文件
    for db_path in db_paths - set(disk_rel_paths.keys()):
        index.delete_document(db_path)
        stats["deleted"] += 1
        print(f"  [DEL] {db_path}")

    # 新增/更新
    for rel_path, abs_path in sorted(disk_rel_paths.items()):
        try:
            changed = index_file(abs_path, embedder, index, vault_dir)
            if changed:
                existing = db_paths and rel_path in db_paths
                if existing:
                    stats["updated"] += 1
                    label = "UPD"
                else:
                    stats["new"] += 1
                    label = "NEW"
                print(f"  [{label}] {rel_path}")
            else:
                stats["skipped"] += 1
        except Exception as e:
            print(f"  [ERROR] {rel_path}: {e}", file=sys.stderr)

    return stats


def rebuild_index(
    embedder: BaseEmbedder,
    index: IndexManager,
    vault_dir: Path = SOLUTION_DIR,
) -> dict[str, int]:
    """重建全部索引：清空数据库，重新扫描所有文件。"""
    index.conn.execute("DELETE FROM chunks")
    index.conn.execute("DELETE FROM documents")
    index.conn.commit()

    stats = {"new": 0, "updated": 0, "skipped": 0, "deleted": 0}

    disk_files = _scan_markdown_files(vault_dir)
    print(f"扫描到 {len(disk_files)} 个 Markdown 文件")

    for abs_path in sorted(disk_files):
        try:
            rel_path = abs_path.relative_to(vault_dir).as_posix() if abs_path.is_relative_to(vault_dir) else abs_path.as_posix()
            index_file(abs_path, embedder, index, vault_dir)
            stats["new"] += 1
            print(f"  [NEW] {rel_path}")
        except Exception as e:
            print(f"  [ERROR] {abs_path}: {e}", file=sys.stderr)

    return stats


# ---------------------------------------------------------------------------
# 输出格式化
# ---------------------------------------------------------------------------

def format_search_results(results: list[SearchResult], query: str, json_output: bool = False) -> str:
    """格式化搜索结果。"""
    if json_output:
        output = []
        for r in results:
            output.append({
                "path": r.path,
                "wikilink": r.wikilink,
                "title": r.title,
                "bucket": r.bucket,
                "bucket_label": r.bucket_label,
                "matched_terms": r.matched_terms,
                "semantic_score": r.semantic_score,
                "heading": r.heading,
                "snippet": r.snippet,
                "project": r.project,
            })
        return json.dumps(output, ensure_ascii=False, indent=2)

    lines = [f"## 搜索结果：{query}", ""]
    if not results:
        lines.append("未找到匹配结果。")
        return "\n".join(lines)

    for i, r in enumerate(results, 1):
        title = r.title or r.path
        lines.append(f"### {i}. {r.wikilink} — {title}")
        if r.project:
            lines.append(f"   项目: {r.project}")
        lines.append(f"   命中类型: {r.bucket_label}")
        if r.matched_terms:
            lines.append(f"   匹配词: {', '.join(r.matched_terms)}")
        lines.append(f"   语义相似度: {r.semantic_score}")
        if r.heading and r.heading != title:
            lines.append(f"   章节: {r.heading}")
        if r.snippet:
            lines.append(f"   片段: {r.snippet}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Obsidian 知识库混合检索引擎",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
        示例:
          kb_search.py index --incremental
          kb_search.py search "如何接着上次没有完成的工作" --top-k 5
          kb_search.py rebuild
          kb_search.py search "checkpoint" --json --top-k 3
        """),
    )
    parser.add_argument("--vault", default=str(SOLUTION_DIR), help="知识库目录")
    parser.add_argument("--cache-dir", default=str(CACHE_DIR), help="索引缓存目录")
    parser.add_argument("--fake-embedder", action="store_true", help="使用伪向量器（测试用）")

    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # index
    index_parser = subparsers.add_parser("index", help="增量索引")
    index_parser.add_argument("--incremental", action="store_true", default=True, help="增量模式（默认）")

    # search
    search_parser = subparsers.add_parser("search", help="混合搜索")
    search_parser.add_argument("query", help="搜索查询")
    search_parser.add_argument("--top-k", type=int, default=TOP_K_DEFAULT, help=f"返回结果数（默认 {TOP_K_DEFAULT}）")
    search_parser.add_argument("--json", action="store_true", help="JSON 格式输出")
    search_parser.add_argument("--no-semantic", action="store_true", help="仅词法检索")
    search_parser.add_argument("--skip-index", action="store_true", help="跳过搜索前的增量索引")

    # rebuild
    rebuild_parser = subparsers.add_parser("rebuild", help="重建全部索引")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    vault_dir = Path(args.vault).resolve()
    cache_dir = Path(args.cache_dir).resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    db_path = cache_dir / "kb_index.db"

    if args.command in ("index", "rebuild", "search"):
        if not vault_dir.exists():
            print(f"错误: 知识库目录不存在: {vault_dir}", file=sys.stderr)
            sys.exit(1)

    if args.command == "index":
        # 创建 embedder
        embedder = try_create_embedder(cache_dir=cache_dir) if not args.fake_embedder else create_embedder(fake=True)
        if embedder is None:
            print("错误: 无法加载 embedding 模型。请确保已安装 sentence-transformers。", file=sys.stderr)
            print("  pip3 install sentence-transformers", file=sys.stderr)
            sys.exit(1)

        with IndexManager(db_path) as index:
            stats = index_incremental(embedder, index, vault_dir)
            index.conn.commit()

        print(f"\n完成: {stats['new']} 新增, {stats['updated']} 更新, "
              f"{stats['skipped']} 跳过, {stats['deleted']} 删除")

    elif args.command == "search":
        # 尝试加载 embedder（可降级）
        embedder = None
        if not args.no_semantic:
            if args.fake_embedder:
                embedder = create_embedder(fake=True)
            else:
                embedder = try_create_embedder(cache_dir=cache_dir)
            if embedder is None:
                print("  [INFO] embedding 模型不可用，退回纯词法检索", file=sys.stderr)

        # 搜索前自动增量索引（静默，除非有变化）
        if not args.skip_index and embedder is not None:
            try:
                with IndexManager(db_path) as idx_mgr:
                    stats = index_incremental(embedder, idx_mgr, vault_dir)
                    idx_mgr.conn.commit()
                if any(v > 0 for v in stats.values()):
                    print(f"  [INDEX] {stats['new']} 新增, {stats['updated']} 更新, "
                          f"{stats['deleted']} 删除", file=sys.stderr)
            except Exception:
                pass  # 增量索引失败不影响搜索

        with IndexManager(db_path) as index:
            if embedder is not None:
                results = hybrid_search(args.query, embedder, index, args.top_k, vault_dir)
            else:
                # 纯词法检索降级
                lexical_hits = lexical_search(args.query, index, vault_dir)
                metas = index.all_document_metas()
                results = []
                for h in lexical_hits[:args.top_k]:
                    meta = metas.get(h.path, {})
                    results.append(SearchResult(
                        path=h.path,
                        wikilink=wikilink_from_path(vault_dir / h.path),
                        bucket=h.bucket,
                        bucket_label=BUCKET_LABELS[h.bucket],
                        matched_terms=h.matched_terms,
                        semantic_score=0.0,
                        heading=h.heading or meta.get("title"),
                        snippet=h.snippet or "",
                        title=meta.get("title"),
                        project=meta.get("project"),
                    ))

            print(format_search_results(results, args.query, json_output=args.json))

    elif args.command == "rebuild":
        embedder = try_create_embedder(cache_dir=cache_dir) if not args.fake_embedder else create_embedder(fake=True)
        if embedder is None:
            print("错误: 无法加载 embedding 模型。请确保已安装 sentence-transformers。", file=sys.stderr)
            print("  pip3 install sentence-transformers", file=sys.stderr)
            sys.exit(1)

        print(f"重建索引: {db_path}")
        with IndexManager(db_path) as index:
            stats = rebuild_index(embedder, index, vault_dir)
            index.conn.commit()

        print(f"\n完成: {stats['new']} 文件已索引")


if __name__ == "__main__":
    main()
