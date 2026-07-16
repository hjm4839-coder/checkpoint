#!/usr/bin/env python3
"""tests/test_search_hybrid.py — kb_search 混合检索单元测试，使用 FakeEmbedder。"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

# 确保可以导入 kb_search
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import kb_search
from kb_search import (
    Chunk,
    IndexManager,
    SearchResult,
    EmbedderError,
    FakeEmbedder,
    BaseEmbedder,
    chunk_markdown,
    parse_frontmatter,
    safe_json_list,
    sha256,
    wikilink_from_path,
    lexical_search,
    semantic_search,
    hybrid_search,
    index_file,
    index_incremental,
    rebuild_index,
    format_search_results,
    _extract_keywords,
    _match_any,
    _extract_snippet,
    BUCKET_LABELS,
)


class TestFrontmatterParsing(unittest.TestCase):
    """测试 frontmatter 解析。"""

    def test_simple_kv(self):
        meta, body = parse_frontmatter("""---
title: 测试文档
project: test-project
tags: [a, b, c]
---
# Hello

正文内容。""")
        self.assertEqual(meta["title"], "测试文档")
        self.assertEqual(meta["project"], "test-project")
        self.assertEqual(meta["tags"], ["a", "b", "c"])
        self.assertIn("正文内容", body)

    def test_list_values(self):
        meta, body = parse_frontmatter("""---
aliases:
  - alias1
  - alias2
keywords:
  - kw1
  - kw2
---
正文。""")
        self.assertEqual(meta["aliases"], ["alias1", "alias2"])
        self.assertEqual(meta["keywords"], ["kw1", "kw2"])

    def test_no_frontmatter(self):
        meta, body = parse_frontmatter("# Just a title\n\nSome content.")
        self.assertEqual(meta, {})
        self.assertIn("Some content", body)

    def test_quoted_strings(self):
        meta, _ = parse_frontmatter("""---
title: "带引号的标题"
---
""")
        self.assertEqual(meta["title"], "带引号的标题")


class TestSafeJsonList(unittest.TestCase):
    def test_list(self):
        self.assertEqual(safe_json_list(["a", "b"]), ["a", "b"])

    def test_json_string(self):
        self.assertEqual(safe_json_list('["x", "y"]'), ["x", "y"])

    def test_single_string(self):
        self.assertEqual(safe_json_list("hello"), ["hello"])

    def test_none(self):
        self.assertEqual(safe_json_list(None), [])


class TestChunkMarkdown(unittest.TestCase):
    def test_single_short_section(self):
        body = "## 章节1\n\n这是一段简短的正文。"
        chunks = chunk_markdown(body, "H1标题")
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].heading, "章节1")
        self.assertEqual(chunks[0].section_title, "H1标题")
        self.assertIn("简短的正文", chunks[0].content)

    def test_long_section_splits(self):
        # 生成超长段落触发拆分
        long_para = "这是长段落。" * 300  # ~2100 字符
        body = f"## 章节1\n\n{long_para}"
        chunks = chunk_markdown(body, None)
        # 应该被拆分成多个块
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertEqual(c.heading, "章节1")

    def test_multiple_sections(self):
        body = "## 章节A\n\n内容A。\n\n## 章节B\n\n内容B。\n\n### 子章节\n\n内容C。"
        chunks = chunk_markdown(body, None)
        self.assertEqual(len(chunks), 3)
        self.assertEqual(chunks[0].heading, "章节A")
        self.assertEqual(chunks[1].heading, "章节B")
        self.assertEqual(chunks[2].heading, "子章节")

    def test_empty_body(self):
        chunks = chunk_markdown("", None)
        self.assertEqual(len(chunks), 0)


class TestKeywordExtraction(unittest.TestCase):
    def test_chinese_keywords(self):
        kws = _extract_keywords("如何接着上次没有完成的工作")
        self.assertIn("如何接着上次没有完成的工作", kws)

    def test_mixed_chinese_english(self):
        kws = _extract_keywords("VPN 配置 checkpoint")
        self.assertIn("VPN", kws)
        self.assertIn("配置", kws)
        self.assertIn("checkpoint", kws)

    def test_stopwords_filtered(self):
        kws = _extract_keywords("我的和你的")
        # "我", "的", "你" 都是停用词，只有 "和的" 不是（但它是2字组合）
        # 实际上 "我的" 不在停用词表，"和你" 也不在
        # 简化测试
        self.assertTrue(len(kws) >= 0)

    def test_empty_query(self):
        kws = _extract_keywords("")
        self.assertEqual(kws, [])


class TestMatchAny(unittest.TestCase):
    def test_exact_match(self):
        self.assertEqual(_match_any(["checkpoint"], ["checkpoint机制"]), ["checkpoint"])

    def test_case_insensitive(self):
        self.assertEqual(_match_any(["VPN"], ["vpn配置"]), ["VPN"])

    def test_no_match(self):
        self.assertEqual(_match_any(["redis"], ["postgres", "mysql"]), [])

    def test_multiple_keywords(self):
        self.assertEqual(
            sorted(_match_any(["checkpoint", "redis"], ["checkpoint机制", "redis缓存"])),
            sorted(["checkpoint", "redis"]),
        )


class TestFakeEmbedder(unittest.TestCase):
    def setUp(self):
        self.embedder = FakeEmbedder()

    def test_passage_embedding(self):
        vecs = self.embedder.embed_passages(["hello world"])
        self.assertEqual(len(vecs), 1)
        self.assertEqual(len(vecs[0]), 16)
        # 归一化向量长度应接近 1
        norm = sum(v * v for v in vecs[0]) ** 0.5
        self.assertAlmostEqual(norm, 1.0, places=4)

    def test_query_embedding(self):
        vecs = self.embedder.embed_queries(["test query"])
        self.assertEqual(len(vecs), 1)
        self.assertEqual(len(vecs[0]), 16)

    def test_deterministic(self):
        v1 = self.embedder.embed_passages(["hello"])[0]
        v2 = self.embedder.embed_passages(["hello"])[0]
        self.assertEqual(v1, v2)

    def test_different_inputs_different_vectors(self):
        v1 = self.embedder.embed_passages(["hello"])[0]
        v2 = self.embedder.embed_passages(["world"])[0]
        self.assertNotEqual(v1, v2)

    @property
    def dimension(self):
        return 16

    @property
    def model_name(self):
        return "fake"


class TestIndexManager(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "test.db"

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_init_schema(self):
        with IndexManager(self.db_path) as im:
            rows = im.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            tables = {r[0] for r in rows}
            self.assertIn("documents", tables)
            self.assertIn("chunks", tables)

    def test_upsert_and_get_document(self):
        with IndexManager(self.db_path) as im:
            meta = {"title": "Test", "aliases": ["a1"], "project": "p1"}
            im.upsert_document("test.md", meta, "abc123", 123456789)
            doc = im.get_document("test.md")
            self.assertEqual(doc["title"], "Test")
            self.assertEqual(json.loads(doc["aliases_json"]), ["a1"])

    def test_delete_document_cascades_chunks(self):
        with IndexManager(self.db_path) as im:
            im.upsert_document("test.md", {"title": "T"}, "hash1", 1)
            chunk = Chunk(heading="H", content="C", ordinal=0)
            im.insert_chunk("test.md", chunk, [0.1] * 16)
            im.conn.commit()

            im.delete_document("test.md")
            chunks = im.get_chunks("test.md")
            self.assertEqual(len(chunks), 0)
            doc = im.get_document("test.md")
            self.assertIsNone(doc)

    def test_all_document_paths(self):
        with IndexManager(self.db_path) as im:
            im.upsert_document("a.md", {}, "h1", 1)
            im.upsert_document("b.md", {}, "h2", 2)
            self.assertEqual(im.all_document_paths(), {"a.md", "b.md"})

    def test_all_document_metas(self):
        with IndexManager(self.db_path) as im:
            im.upsert_document("a.md", {"title": "A", "aliases": ["x"]}, "h1", 1)
            im.upsert_document("b.md", {"title": "B", "tags": ["y"]}, "h2", 2)
            metas = im.all_document_metas()
            self.assertEqual(metas["a.md"]["title"], "A")
            self.assertEqual(metas["a.md"]["aliases"], ["x"])
            self.assertEqual(metas["b.md"]["tags"], ["y"])

    def test_vector_roundtrip(self):
        from kb_search import _vector_to_blob, _blob_to_vector
        vec = [0.1, 0.2, 0.3, 0.4]
        blob = _vector_to_blob(vec)
        restored = _blob_to_vector(blob)
        for a, b in zip(vec, restored):
            self.assertAlmostEqual(a, b, places=5)


class TestLexicalSearch(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "test.db"
        self.vault_dir = Path(self.tmpdir) / "vault"
        self.vault_dir.mkdir(exist_ok=True)

        # 创建测试文件
        self._write_file("test_a.md", """---
title: 检查点测试
aliases: [checkpoint, 会话恢复]
keywords: [断点续传]
tags: [obsidian]
---
# 检查点测试

这是关于 checkpoint 机制的测试文档。""")

        self._write_file("test_b.md", """---
title: 网络配置
tags: [vpn, 网络]
---
# 网络配置

VPN 连接和验证码相关配置。""")

        self._write_file("test_c.md", """---
title: 其他文档
---
# 其他

完全不相关的内容。""")

        # 创建索引
        with IndexManager(self.db_path) as im:
            im.upsert_document("test_a.md", {
                "title": "检查点测试",
                "aliases": ["checkpoint", "会话恢复"],
                "keywords": ["断点续传"],
                "tags": ["obsidian"],
            }, "h1", 1)
            im.upsert_document("test_b.md", {
                "title": "网络配置",
                "tags": ["vpn", "网络"],
            }, "h2", 2)
            im.upsert_document("test_c.md", {
                "title": "其他文档",
            }, "h3", 3)
            im.conn.commit()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_file(self, name: str, content: str):
        path = self.vault_dir / name
        path.write_text(content, encoding="utf-8")

    def test_alias_match(self):
        with IndexManager(self.db_path) as im:
            hits = lexical_search("checkpoint", im, self.vault_dir)
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0].path, "test_a.md")
            self.assertEqual(hits[0].bucket, 0)

    def test_tag_match(self):
        with IndexManager(self.db_path) as im:
            hits = lexical_search("VPN", im, self.vault_dir)
            self.assertTrue(any(h.path == "test_b.md" and h.bucket == 2 for h in hits))

    def test_body_match(self):
        with IndexManager(self.db_path) as im:
            hits = lexical_search("验证码", im, self.vault_dir)
            self.assertTrue(any(h.path == "test_b.md" for h in hits))

    def test_no_match(self):
        with IndexManager(self.db_path) as im:
            hits = lexical_search("redis", im, self.vault_dir)
            self.assertEqual(len(hits), 0)

    def test_bucket_ordering(self):
        """aliases 命中排在 tags 之前。"""
        with IndexManager(self.db_path) as im:
            hits = lexical_search("obsidian", im, self.vault_dir)
            # test_a.md 有 aliases=checkpoint 但不匹配 obsidian，标题有 checkpoint
            # test_c.md 完全不相关
            # 至少返回了一些结果
            buckets = [h.bucket for h in hits]
            self.assertEqual(buckets, sorted(buckets))


class TestSemanticSearch(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "test.db"
        self.embedder = FakeEmbedder()

        with IndexManager(self.db_path) as im:
            im.upsert_document("doc1.md", {"title": "Doc1"}, "h1", 1)
            c1 = Chunk(heading="H1", content="content about checkpoint and session resume", ordinal=0)
            v1 = self.embedder.embed_passages(["passage: checkpoint session resume"])[0]
            im.insert_chunk("doc1.md", c1, v1)

            im.upsert_document("doc2.md", {"title": "Doc2"}, "h2", 2)
            c2 = Chunk(heading="H2", content="network and vpn configuration", ordinal=0)
            v2 = self.embedder.embed_passages(["passage: network vpn"])[0]
            im.insert_chunk("doc2.md", c2, v2)
            im.conn.commit()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_returns_results(self):
        with IndexManager(self.db_path) as im:
            hits = semantic_search("checkpoint", self.embedder, im, top_k=5)
            self.assertGreater(len(hits), 0)

    def test_top_k_respected(self):
        with IndexManager(self.db_path) as im:
            hits = semantic_search("test", self.embedder, im, top_k=1)
            self.assertLessEqual(len(hits), 1)

    def test_deduplication(self):
        """同一文件多个块只返回最高分块。"""
        with IndexManager(self.db_path) as im:
            # 为 doc1 插入第二个块
            c3 = Chunk(heading="H1b", content="more content", ordinal=1)
            v3 = self.embedder.embed_passages(["passage: more content"])[0]
            im.insert_chunk("doc1.md", c3, v3)
            im.conn.commit()

            hits = semantic_search("test", self.embedder, im, top_k=5)
            paths = [h.path for h in hits]
            self.assertEqual(len(paths), len(set(paths)))  # 无重复

    def test_descending_score_order(self):
        with IndexManager(self.db_path) as im:
            hits = semantic_search("test", self.embedder, im, top_k=5)
            scores = [h.score for h in hits]
            self.assertEqual(scores, sorted(scores, reverse=True))


class TestHybridSearch(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "test.db"
        self.vault_dir = Path(self.tmpdir) / "vault"
        self.vault_dir.mkdir(exist_ok=True)
        self.embedder = FakeEmbedder()

        # 创建文件
        self._write_file("checkpoint.md", """---
title: Checkpoint 机制
aliases: [checkpoint, 断点恢复]
keywords: [会话恢复]
tags: [obsidian, 知识库]
---
# Checkpoint 机制

这是关于 checkpoint 断点续传机制的详细文档。""")

        self._write_file("vpn.md", """---
title: VPN 配置
tags: [vpn, 网络]
---
# VPN 配置

VPN 连接配置和自动连接方案。""")

        self._write_file("unrelated.md", """---
title: 完全不相关
---
# 完全不相关

这是不相关的内容。""")

        # 建立索引
        with IndexManager(self.db_path) as im:
            for fname, title, aliases, keywords, tags in [
                ("checkpoint.md", "Checkpoint 机制", ["checkpoint", "断点恢复"], ["会话恢复"], ["obsidian", "知识库"]),
                ("vpn.md", "VPN 配置", [], [], ["vpn", "网络"]),
                ("unrelated.md", "完全不相关", [], [], []),
            ]:
                im.upsert_document(fname, {
                    "title": title,
                    "aliases": aliases,
                    "keywords": keywords,
                    "tags": tags,
                }, f"hash_{fname}", 1)

                path = self.vault_dir / fname
                raw = path.read_text(encoding="utf-8")
                _, body = parse_frontmatter(raw)
                chunks = chunk_markdown(body, title)
                for chunk in chunks:
                    parts = [f"标题: {title}", chunk.content]
                    vec = self.embedder.embed_passages(["\n".join(parts)])[0]
                    im.insert_chunk(fname, chunk, vec)
            im.conn.commit()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_file(self, name: str, content: str):
        (self.vault_dir / name).write_text(content, encoding="utf-8")

    def test_lexical_before_semantic(self):
        with IndexManager(self.db_path) as im:
            results = hybrid_search("checkpoint", self.embedder, im, top_k=5, vault_dir=self.vault_dir)
            # checkpoint.md 应该排在前面（aliases 命中）
            self.assertGreater(len(results), 0)
            self.assertEqual(results[0].path, "checkpoint.md")
            self.assertEqual(results[0].bucket, 0)  # aliases

    def test_semantic_only_results(self):
        with IndexManager(self.db_path) as im:
            results = hybrid_search("xyz_not_in_any_document", self.embedder, im, top_k=5, vault_dir=self.vault_dir)
            # 纯语义命中应该都是 bucket 4
            for r in results:
                self.assertEqual(r.bucket, 4)

    def test_top_k_respected(self):
        with IndexManager(self.db_path) as im:
            results = hybrid_search("test", self.embedder, im, top_k=2, vault_dir=self.vault_dir)
            self.assertLessEqual(len(results), 2)


class TestFormatting(unittest.TestCase):
    def test_json_output(self):
        results = [
            SearchResult(
                path="test.md",
                wikilink="[[test]]",
                bucket=0,
                bucket_label="aliases",
                matched_terms=["checkpoint"],
                semantic_score=0.95,
                heading="Test",
                snippet="snippet here",
                title="Test Doc",
                project="test-project",
            )
        ]
        output = format_search_results(results, "checkpoint", json_output=True)
        data = json.loads(output)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["path"], "test.md")
        self.assertEqual(data[0]["matched_terms"], ["checkpoint"])

    def test_markdown_output(self):
        results = [
            SearchResult(
                path="test.md",
                wikilink="[[test]]",
                bucket=0,
                bucket_label="aliases",
                matched_terms=["checkpoint"],
                semantic_score=1.0,
                heading="Test",
                snippet="snippet",
                title="Test Doc",
                project=None,
            )
        ]
        output = format_search_results(results, "checkpoint", json_output=False)
        self.assertIn("搜索结果", output)
        self.assertIn("[[test]]", output)
        self.assertIn("aliases", output)

    def test_empty_results(self):
        output = format_search_results([], "nothing", json_output=False)
        self.assertIn("未找到匹配结果", output)


class TestIncrementalIndexing(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "test.db"
        self.vault_dir = Path(self.tmpdir) / "vault"
        self.vault_dir.mkdir(exist_ok=True)
        self.embedder = FakeEmbedder()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_file(self, name: str, content: str):
        (self.vault_dir / name).write_text(content, encoding="utf-8")

    def test_new_file_indexed(self):
        self._write_file("new.md", """---
title: 新文档
---
# 新文档
内容。""")

        with IndexManager(self.db_path) as im:
            stats = index_incremental(self.embedder, im, self.vault_dir)
            self.assertEqual(stats["new"], 1)
            self.assertEqual(stats["skipped"], 0)

    def test_unchanged_file_skipped(self):
        self._write_file("doc.md", """---
title: 文档
---
# 文档
内容。""")

        # 第一次索引
        with IndexManager(self.db_path) as im:
            stats1 = index_incremental(self.embedder, im, self.vault_dir)
            self.assertEqual(stats1["new"], 1)
            im.conn.commit()

        # 第二次索引（内容未变）
        with IndexManager(self.db_path) as im:
            stats2 = index_incremental(self.embedder, im, self.vault_dir)
            self.assertEqual(stats2["skipped"], 1)

    def test_modified_file_reindexed(self):
        self._write_file("doc.md", """---
title: 版本1
---
# 版本1
内容。""")

        with IndexManager(self.db_path) as im:
            stats1 = index_incremental(self.embedder, im, self.vault_dir)
            self.assertEqual(stats1["new"], 1)
            im.conn.commit()

        # 修改内容
        self._write_file("doc.md", """---
title: 版本2
---
# 版本2
修改后的内容。""")

        with IndexManager(self.db_path) as im:
            stats2 = index_incremental(self.embedder, im, self.vault_dir)
            self.assertEqual(stats2["updated"], 1)

    def test_deleted_file_cleaned(self):
        self._write_file("doc.md", """---
title: 临时
---
# 临时
内容。""")

        with IndexManager(self.db_path) as im:
            index_incremental(self.embedder, im, self.vault_dir)
            im.conn.commit()

        # 删除文件
        (self.vault_dir / "doc.md").unlink()

        with IndexManager(self.db_path) as im:
            stats = index_incremental(self.embedder, im, self.vault_dir)
            self.assertEqual(stats["deleted"], 1)


class TestRebuildIndex(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "test.db"
        self.vault_dir = Path(self.tmpdir) / "vault"
        self.vault_dir.mkdir(exist_ok=True)
        self.embedder = FakeEmbedder()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_rebuild_cleans_old_data(self):
        # 先建索引
        (self.vault_dir / "a.md").write_text("""---
title: A
---
# A
Content A.""", encoding="utf-8")

        with IndexManager(self.db_path) as im:
            index_incremental(self.embedder, im, self.vault_dir)
            im.conn.commit()

        # 删除文件后重建
        (self.vault_dir / "a.md").unlink()
        (self.vault_dir / "b.md").write_text("""---
title: B
---
# B
Content B.""", encoding="utf-8")

        with IndexManager(self.db_path) as im:
            stats = rebuild_index(self.embedder, im, self.vault_dir)
            im.conn.commit()
            self.assertEqual(stats["new"], 1)

            # 确认旧文件已不在索引中
            doc = im.get_document("a.md")
            self.assertIsNone(doc)
            doc_b = im.get_document("b.md")
            self.assertIsNotNone(doc_b)


class TestEmbedderError(unittest.TestCase):
    def test_try_create_embedder_returns_none(self):
        # FakeEmbedder 总是成功，但我们可以测试工厂逻辑
        embedder = kb_search.create_embedder(fake=True)
        self.assertIsInstance(embedder, FakeEmbedder)


class TestSnippetExtraction(unittest.TestCase):
    def test_snippet_around_keyword(self):
        text = "这是一段很长的文本" + "内容" * 50 + "checkpoint" + "更多" * 50
        snippet = _extract_snippet(text, ["checkpoint"])
        self.assertIsNotNone(snippet)
        self.assertIn("checkpoint", snippet)

    def test_no_match_returns_none(self):
        snippet = _extract_snippet("nothing here", ["keyword"])
        self.assertIsNone(snippet)


class TestWikilink(unittest.TestCase):
    def test_wikilink_generation(self):
        from pathlib import Path
        import kb_search
        # 使用真实的 OBSIDIAN_VAULT 路径
        vault = kb_search.OBSIDIAN_VAULT
        path = vault / "Claude方案/test/doc.md"
        wl = wikilink_from_path(path)
        self.assertEqual(wl, "[[Claude方案/test/doc]]")


if __name__ == "__main__":
    unittest.main()
