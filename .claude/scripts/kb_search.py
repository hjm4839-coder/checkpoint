#!/usr/bin/env python3
"""
本地混合语义检索引擎 — 为 Claude Code /search 技能提供底层支持。

用法:
  python3 kb_search.py index [--incremental]    # 增量索引
  python3 kb_search.py search "<query>" [--top-k 5]  # 混合搜索
  python3 kb_search.py rebuild                   # 重建全部索引

架构:
  词法检索 (aliases > keywords > tags > body) + 向量语义召回
  模型: intfloat/multilingual-e5-small (384 dim)
  存储: SQLite + NumPy BLOB, 零外部向量数据库
"""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

# ============================================================
# 配置
# ============================================================

VAULT_ROOT = Path(os.environ.get("OBSIDIAN_VAULT", os.path.expanduser("~/obsidian/知识库")))
PLANS_DIR = VAULT_ROOT / "Claude方案"
CACHE_DIR = Path(os.path.expanduser("~/Library/Caches/claude-obsidian-semantic"))
DB_PATH = CACHE_DIR / "kb_index.db"
CHUNK_SIZE = 800          # 每个文本块的目标字符数
CHUNK_OVERLAP = 100        # 相邻块重叠字符数
DEFAULT_TOP_K = 5
EMBED_DIM = 384


# ============================================================
# SQLite 表结构
# ============================================================

CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS documents (
    path TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL,
    mtime_ns INTEGER NOT NULL,
    title TEXT,
    aliases_json TEXT,
    keywords_json TEXT,
    tags_json TEXT,
    project TEXT
);

CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    heading TEXT,
    content TEXT NOT NULL,
    embedding BLOB NOT NULL,
    FOREIGN KEY(path) REFERENCES documents(path)
);

CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(path);
"""


# ============================================================
# Embedding 模型 (懒加载)
# ============================================================

_embed_model = None


def _get_embed_model():
    """懒加载 sentence-transformers 模型，进程级单例。使用本地缓存，不访问网络。"""
    global _embed_model
    if _embed_model is None:
        try:
            import os as _os
            # 禁止联网, 只用本地缓存
            _os.environ.setdefault("HF_HUB_OFFLINE", "1")
            from sentence_transformers import SentenceTransformer
            _embed_model = SentenceTransformer(
                "intfloat/multilingual-e5-small",
                cache_folder=str(CACHE_DIR),
                local_files_only=True,
            )
        except Exception as e:
            print(f"[kb-search] WARNING: Failed to load embedding model: {e}", file=sys.stderr)
            print("[kb-search] Falling back to lexical-only search.", file=sys.stderr)
            _embed_model = False
    return _embed_model if _embed_model is not False else None


def embed_batch(texts: list[str]) -> list[list[float]]:
    """批量生成向量。e5 模型需要 'query:' / 'passage:' 前缀。"""
    model = _get_embed_model()
    if model is None:
        return []
    prefixed = [f"passage: {t}" for t in texts]
    vecs = model.encode(prefixed, normalize_embeddings=True, show_progress_bar=False)
    return vecs.tolist()


def embed_query(query: str) -> list[float]:
    """生成查询向量。"""
    model = _get_embed_model()
    if model is None:
        return []
    from sentence_transformers import SentenceTransformer
    vec = model.encode([f"query: {query}"], normalize_embeddings=True, show_progress_bar=False)
    return vec[0].tolist()


# ============================================================
# Markdown 解析
# ============================================================

def _parse_frontmatter(text: str) -> dict:
    """提取 YAML frontmatter 核心字段。"""
    result = {"aliases": [], "keywords": [], "tags": [], "project": ""}
    m = re.match(r'^---\n(.*?)\n---', text, re.DOTALL)
    if not m:
        return result
    fm = m.group(1)
    for key, field in [("aliases", "aliases"), ("keywords", "keywords"),
                        ("tags", "tags"), ("project", "project")]:
        val = _parse_frontmatter_list(fm, key)
        if field == "project":
            result[field] = val[0] if val else ""
        else:
            result[field] = val
    return result


def _parse_frontmatter_list(text: str, key: str) -> list:
    """从 frontmatter 文本中解析 YAML 列表字段。"""
    for line in text.split('\n'):
        line = line.strip()
        # YAML: key: [a, b, c]
        m = re.match(rf'^{key}:\s*\[(.+)\]', line)
        if m:
            return [v.strip().strip('"').strip("'") for v in m.group(1).split(',') if v.strip()]
        # YAML: key: "single value"
        m = re.match(rf'^{key}:\s*"([^"]+)"', line)
        if m:
            return [m.group(1)]
    return []


def _extract_title(text: str) -> str:
    """提取文档 H1 标题。"""
    m = re.search(r'^#\s+(.+)', text, re.MULTILINE)
    return m.group(1).strip() if m else ""


def _split_chunks(text: str, meta: dict) -> list[dict]:
    """按 ## 标题切块，过长块再按段落拆分，相邻块有重叠。"""
    title = meta.get("title", "")
    chunks = []

    # 移除 frontmatter
    body = re.sub(r'^---\n.*?\n---\n', '', text, flags=re.DOTALL)

    # 按 ## 标题切分
    sections = re.split(r'\n(?=##\s)', body)
    for sec in sections:
        h = ""
        m = re.match(r'^##\s+(.+)', sec)
        if m:
            h = m.group(1).strip()

        content = sec.strip()
        if not content:
            continue

        # 拼接标题上下文
        full = f"# {title}\n## {h}\n\n{content}" if h else f"# {title}\n\n{content}"

        if len(full) <= CHUNK_SIZE:
            chunks.append({"heading": h, "content": full})
        else:
            # 长文档按段落拆分 + 重叠
            paras = re.split(r'\n\n+', content)
            buf = f"# {title}\n## {h}\n\n" if h else f"# {title}\n\n"
            for p in paras:
                if len(buf) + len(p) > CHUNK_SIZE and buf:
                    chunks.append({"heading": h, "content": buf.strip()})
                    # overlap: keep last sentence-ish
                    buf = buf[-CHUNK_OVERLAP:] if len(buf) > CHUNK_OVERLAP else ""
                buf += p + "\n\n"
            if buf.strip():
                chunks.append({"heading": h, "content": buf.strip()})

    return chunks


# ============================================================
# 增量索引
# ============================================================

def _compute_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _index_file(db: sqlite3.Connection, path: Path, model_available: bool):
    """索引单个 Markdown 文件（删除旧条目 + 重建）。"""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return

    meta = _parse_frontmatter(text)
    meta["title"] = _extract_title(text)
    sha = _compute_sha256(path)
    mtime = int(os.path.getmtime(str(path)) * 1e9)

    rel = str(_rel_path(path))

    # 删除旧条目
    db.execute("DELETE FROM chunks WHERE path = ?", (rel,))
    db.execute("DELETE FROM documents WHERE path = ?", (rel,))

    # 写入文档元数据
    db.execute(
        "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (rel, sha, mtime, meta["title"],
         json.dumps(meta["aliases"], ensure_ascii=False),
         json.dumps(meta["keywords"], ensure_ascii=False),
         json.dumps(meta["tags"], ensure_ascii=False),
         meta.get("project", "")),
    )

    # 切块
    chunks = _split_chunks(text, meta)
    if not chunks:
        return

    # 批量向量化
    if model_available:
        texts = [c["content"] for c in chunks]
        vecs = embed_batch(texts)
    else:
        vecs = [[]] * len(chunks)

    # 写入块
    for i, (c, v) in enumerate(zip(chunks, vecs)):
        emb_blob = _vec_to_blob(v) if v else b""
        db.execute(
            "INSERT INTO chunks (path, ordinal, heading, content, embedding) VALUES (?, ?, ?, ?, ?)",
            (rel, i, c["heading"], c["content"], emb_blob),
        )


def index_all(incremental: bool = True):
    """遍历 Clade方案/ 下所有 .md 文件，增量或全量索引。"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    model_available = _get_embed_model() is not None
    if not model_available:
        print("[kb-search] Embedding model unavailable — indexing metadata only (lexical search will still work)")

    db = sqlite3.connect(str(DB_PATH))
    db.executescript(CREATE_TABLES)
    db.execute("PRAGMA journal_mode=WAL")

    files = sorted(PLANS_DIR.rglob("*.md"))
    total = len(files)
    indexed = 0
    skipped = 0

    for i, f in enumerate(files):
        rel = str(_rel_path(f))

        if incremental:
            row = db.execute("SELECT sha256, mtime_ns FROM documents WHERE path = ?", (rel,)).fetchone()
            if row:
                old_sha = row[0]
                try:
                    cur_mtime = int(os.path.getmtime(str(f)) * 1e9)
                except OSError:
                    cur_mtime = 0
                if old_sha == _compute_sha256(f) and row[1] == cur_mtime:
                    skipped += 1
                    continue

        _index_file(db, f, model_available)
        indexed += 1

        if (i + 1) % 20 == 0 or i == total - 1:
            print(f"[kb-search] Indexing: {i+1}/{total} (indexed={indexed}, skipped={skipped})")

    # 清理已删除的文件
    known_paths = {str(_rel_path(f)) for f in files}
    db_paths = {r[0] for r in db.execute("SELECT path FROM documents").fetchall()}
    for stale in db_paths - known_paths:
        db.execute("DELETE FROM chunks WHERE path = ?", (stale,))
        db.execute("DELETE FROM documents WHERE path = ?", (stale,))

    db.commit()
    doc_count = db.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    chunk_count = db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    db.close()
    print(f"[kb-search] Index complete: {doc_count} documents, {chunk_count} chunks ({indexed} updated, {skipped} skipped)")


# ============================================================
# 混合搜索
# ============================================================

def search(query: str, top_k: int = DEFAULT_TOP_K, lexical_only: bool = False):
    """混合搜索：词法优先 + 语义补充。返回按相关性排序的结果列表。"""
    keywords = _extract_search_keywords(query)

    # 确保索引存在
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if not DB_PATH.exists():
        print("[kb-search] No index found. Run 'index' first. Falling back to grep-only search.", file=sys.stderr)
        return _fallback_grep(keywords)

    # 仅 lexical-only 时跳过模型加载
    model_available = False
    if not lexical_only:
        model_available = _get_embed_model() is not None

    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row

    # 1. 词法搜索：aliases / keywords / tags / body
    lexical_hits = {}  # path → lexical_bucket (1=aliases, 2=keywords, 3=tags, 4=body)

    for kw in keywords:
        # aliases
        for row in db.execute(
            "SELECT path FROM documents WHERE aliases_json LIKE ?", (f"%{kw}%",)
        ):
            lexical_hits[row["path"]] = min(lexical_hits.get(row["path"], 5), 1)
        # keywords
        for row in db.execute(
            "SELECT path FROM documents WHERE keywords_json LIKE ?", (f"%{kw}%",)
        ):
            lexical_hits[row["path"]] = min(lexical_hits.get(row["path"], 5), 2)
        # tags
        for row in db.execute(
            "SELECT path FROM documents WHERE tags_json LIKE ?", (f"%{kw}%",)
        ):
            lexical_hits[row["path"]] = min(lexical_hits.get(row["path"], 5), 3)
        # body (chunks)
        for row in db.execute(
            "SELECT DISTINCT path FROM chunks WHERE content LIKE ?", (f"%{kw}%",)
        ):
            if row["path"] not in lexical_hits:
                lexical_hits[row["path"]] = 4

    # 2. 语义搜索 (numpy 批量, 避免 Python 循环)
    semantic_scores = {}
    if model_available and query.strip():
        q_vec = embed_query(query)
        if q_vec:
            import numpy as np
            q = np.array(q_vec, dtype=np.float32)
            # 一次性拉取所有向量, numpy 批量点积
            rows = db.execute(
                "SELECT path, embedding FROM chunks WHERE length(embedding) > 0"
            ).fetchall()
            if rows:
                paths = [r[0] for r in rows]
                # 按 float32 批量解析 BLOB
                all_vecs = np.frombuffer(b"".join(r[1] for r in rows), dtype=np.float32)
                all_vecs = all_vecs.reshape(len(rows), -1)
                # 批量余弦相似度(已归一化 → 点积)
                scores = np.dot(all_vecs, q)
                # 每个文档取最相似块
                for p, s in zip(paths, scores):
                    old = semantic_scores.get(p, -999.0)
                    if s > old:
                        semantic_scores[p] = float(s)

    # 3. 合并排序
    results = []
    all_paths = set(lexical_hits.keys()) | set(semantic_scores.keys())

    for path in all_paths:
        doc = db.execute(
            "SELECT title, aliases_json, keywords_json, tags_json FROM documents WHERE path = ?", (path,)
        ).fetchone()
        if not doc:
            continue

        bucket = lexical_hits.get(path, 5)  # 5 = semantic-only
        sem_score = semantic_scores.get(path, 0.0)

        # 找最佳匹配片段
        snippet = ""
        best_chunk = None
        if bucket <= 4:  # lexical match → 找匹配片段
            for row in db.execute(
                "SELECT heading, content FROM chunks WHERE path = ?", (path,)
            ):
                for kw in keywords:
                    if kw.lower() in row["content"].lower():
                        best_chunk = row
                        break
                if best_chunk:
                    break
        if not best_chunk and sem_score > 0:
            # 语义命中 → 取最相似块
            best_sim = -1
            for row in db.execute("SELECT heading, content, embedding FROM chunks WHERE path = ?", (path,)):
                v = _blob_to_vec(row["embedding"])
                if len(v) == EMBED_DIM:
                    sim = _cosine_sim(q_vec, v)
                    if sim > best_sim:
                        best_sim = sim
                        best_chunk = row
        if best_chunk:
            snippet = best_chunk["content"][:200].replace("\n", " ")

        results.append({
            "path": path,
            "title": doc["title"] or Path(path).stem,
            "bucket": bucket,
            "bucket_label": _bucket_label(bucket),
            "semantic_score": round(sem_score, 4),
            "snippet": snippet,
            "aliases": json.loads(doc["aliases_json"] or "[]"),
            "tags": json.loads(doc["tags_json"] or "[]"),
        })

    # 排序: lexical bucket ASC → semantic_score DESC
    results.sort(key=lambda r: (
        r["bucket"],
        -r["semantic_score"],
    ))

    db.close()

    top = results[:top_k]
    for i, r in enumerate(top):
        label = "aliases" if r["bucket"] == 1 else \
                "keywords" if r["bucket"] == 2 else \
                "tags" if r["bucket"] == 3 else \
                "body" if r["bucket"] == 4 else "semantic"
        print(f"#{i+1} [{label}] [[{r['path']}]]  (sem={r['semantic_score']})")
        if r["snippet"]:
            print(f"    {r['snippet'][:150]}...")
        print()

    return top


# ============================================================
# 辅助函数
# ============================================================

def _rel_path(p: Path) -> Path:
    try:
        return p.relative_to(PLANS_DIR)
    except ValueError:
        return p


def _vec_to_blob(vec: list[float]) -> bytes:
    import struct
    return struct.pack(f'{len(vec)}f', *vec)


def _blob_to_vec(blob: bytes) -> list[float]:
    import struct
    n = len(blob) // 4
    return list(struct.unpack(f'{n}f', blob))


def _cosine_sim(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))  # already normalized


def _bucket_label(b: int) -> str:
    return {1: "aliases", 2: "keywords", 3: "tags", 4: "body"}.get(b, "semantic")


def _extract_search_keywords(query: str) -> list[str]:
    """从查询中提取 1-3 个关键词。"""
    words = re.findall(r'[一-鿿]+|[a-zA-Z0-9._-]+', query)
    # 去停用
    stop = {'的', '了', '是', '在', '我', '有', '和', '就', '不', '人', '都', '一', '一个',
            'the', 'a', 'an', 'is', 'of', 'to', 'in', 'and', 'for', 'on', 'with', 'this'}
    filtered = [w for w in words if w.lower() not in stop]
    return filtered[:3] if filtered else [query[:20]]


def _fallback_grep(keywords: list[str]) -> list:
    """无索引时的 grep 兜底。"""
    import subprocess
    if not keywords:
        return []
    pattern = '|'.join(re.escape(kw) for kw in keywords)
    try:
        out = subprocess.check_output(
            ["grep", "-RIl", "--include=*.md", "-E", pattern, str(PLANS_DIR)],
            stderr=subprocess.DEVNULL, text=True
        ).strip()
        results = []
        for line in out.split("\n")[:DEFAULT_TOP_K]:
            if line:
                rel = _rel_path(Path(line))
                print(f"[grep] [[{rel}]]")
                results.append({"path": str(rel), "bucket": 4, "bucket_label": "body"})
        return results
    except subprocess.CalledProcessError:
        print(f"[kb-search] No results for: {keywords}")
        return []


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="本地混合语义检索引擎")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("index", help="增量索引").add_argument("--incremental", action="store_true", default=True)
    sub.add_parser("rebuild", help="重建全部索引")

    search_p = sub.add_parser("search", help="混合搜索")
    search_p.add_argument("query", help="搜索查询")
    search_p.add_argument("--top-k", type=int, default=DEFAULT_TOP_K, help="返回结果数")
    search_p.add_argument("--lexical-only", action="store_true", help="仅词法搜索（跳过语义）")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command in ("index", "rebuild"):
        index_all(incremental=(args.command == "index"))
    elif args.command == "search":
        search(args.query, args.top_k, getattr(args, 'lexical_only', False))


if __name__ == "__main__":
    main()
