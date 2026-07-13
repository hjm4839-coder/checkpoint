import importlib.util
import json
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "checkpoint.py"
spec = importlib.util.spec_from_file_location("checkpoint", MODULE_PATH)
checkpoint = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = checkpoint
spec.loader.exec_module(checkpoint)

SECRET_VALUES = [
    "fake-bearer-token-1234567890",
    "ab84335a-1248-4a9c-bbd2-a8a3048664e5",
    "sk-testc6a2afdab413df77dfd20dc2f3970527",
    "api-key-value-123456",
    "token-value-123456789",
    "password-value-123456",
    "secret-value-123456",
]


def assert_no_secret(text):
    for value in SECRET_VALUES:
        assert value not in text


def test_redact_sensitive_text_common_secret_shapes():
    text = "\n".join(
        [
            "Authorization: Bearer fake-bearer-token-1234567890",
            "X-Namespace: ab84335a-1248-4a9c-bbd2-a8a3048664e5",
            "key sk-testc6a2afdab413df77dfd20dc2f3970527",
            "api_key=api-key-value-123456",
            "token: token-value-123456789",
            "password=password-value-123456",
            "secret: 'secret-value-123456'",
        ]
    )

    redacted = checkpoint.redact_sensitive_text(text)

    assert_no_secret(redacted)
    assert redacted.count(checkpoint.REDACTION_MARKER) >= 7
    assert "Authorization: Bearer [REDACTED]" in redacted
    assert "X-Namespace: [REDACTED]" in redacted
    assert "api_key=[REDACTED]" in redacted
    assert "secret: '[REDACTED]'" in redacted


def test_redact_sensitive_text_avoids_common_words_and_short_values():
    text = "token password secret api key X-Namespace idea token=short password=1234 secret: tiny"

    redacted = checkpoint.redact_sensitive_text(text)

    assert redacted == text


def test_extract_session_context_redacts_prompts_topic_and_snippets(tmp_path):
    transcript = tmp_path / "session.jsonl"
    entries = [
        {
            "type": "user",
            "message": {
                "content": "请配置 Authorization: Bearer fake-bearer-token-1234567890 和 X-Namespace: ab84335a-1248-4a9c-bbd2-a8a3048664e5",
            },
        },
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "text",
                        "text": "方案如下：使用 api_key=api-key-value-123456 调用服务。",
                    }
                ]
            },
        },
    ]
    transcript.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in entries), encoding="utf-8")

    ctx = checkpoint.extract_session_context(str(transcript))

    joined = "\n".join([ctx["topic"], *ctx["user_prompts"], *ctx["verbal_plan_snippets"]])
    assert_no_secret(joined)
    assert checkpoint.REDACTION_MARKER in joined


def test_generated_session_note_and_index_are_redacted(tmp_path, monkeypatch):
    monkeypatch.setattr(checkpoint, "VAULT_ROOT", tmp_path)
    monkeypatch.setattr(checkpoint, "PLANS_DIR", tmp_path / "Claude方案")
    monkeypatch.setattr(checkpoint, "PLANS_DIR_STR", str(tmp_path / "Claude方案"))
    monkeypatch.setattr(checkpoint, "INDEX_DIR", tmp_path / "Claude方案" / "会话索引")
    monkeypatch.setattr(checkpoint, "NOTE_DIR", tmp_path / "Claude方案" / "会话断点")
    checkpoint.INDEX_DIR.mkdir(parents=True)
    checkpoint.NOTE_DIR.mkdir(parents=True)

    ctx = {
        "topic": "配置 token: token-value-123456789",
        "category": [],
        "tags": [],
        "keywords": [],
        "projects": set(),
        "user_prompts": ["Authorization: Bearer fake-bearer-token-1234567890"],
        "written_files": set(),
        "used_plan_mode": False,
        "verbal_plan_snippets": ["api_key=api-key-value-123456"],
    }

    note = checkpoint.generate_session_note("session123", ctx, "incomplete_archive")
    note_path = checkpoint.NOTE_DIR / "note.md"
    note_path.write_text(note, encoding="utf-8")
    checkpoint.update_daily_index(checkpoint.INDEX_DIR, note_path, "session123", ctx, "incomplete_archive")

    combined = note + "\n" + (checkpoint.INDEX_DIR / f"{checkpoint.datetime.now(checkpoint.timezone.utc).strftime('%Y-%m-%d')}.md").read_text(encoding="utf-8")
    assert_no_secret(combined)
    assert checkpoint.REDACTION_MARKER in combined


def test_redact_written_plan_files_updates_markdown_outputs(tmp_path, monkeypatch):
    monkeypatch.setattr(checkpoint, "PLANS_DIR", tmp_path / "Claude方案")
    plan = checkpoint.PLANS_DIR / "测试项目" / "方案.md"
    plan.parent.mkdir(parents=True)
    plan.write_text(
        "Authorization: Bearer fake-bearer-token-1234567890\nX-Namespace: ab84335a-1248-4a9c-bbd2-a8a3048664e5\n",
        encoding="utf-8",
    )

    checkpoint.redact_written_plan_files({"written_files": {str(plan)}})

    text = plan.read_text(encoding="utf-8")
    assert_no_secret(text)
    assert checkpoint.REDACTION_MARKER in text


def test_project_material_and_knowledge_writes_are_redacted(tmp_path, monkeypatch):
    monkeypatch.setattr(checkpoint, "VAULT_ROOT", tmp_path)
    monkeypatch.setattr(checkpoint, "PLANS_DIR", tmp_path / "Claude方案")
    monkeypatch.setattr(checkpoint, "PLANS_DIR_STR", str(tmp_path / "Claude方案"))
    monkeypatch.setattr(checkpoint, "EXPERIENCE_DIR", tmp_path / "Claude方案" / "AI开发参考")
    project_dir = checkpoint.PLANS_DIR / "测试项目"
    project_dir.mkdir(parents=True)
    checkpoint.EXPERIENCE_DIR.mkdir(parents=True)
    source_doc = project_dir / "方案.md"
    source_doc.write_text("# 方案\npassword=password-value-123456\n", encoding="utf-8")
    session_note = tmp_path / "session.md"
    session_note.write_text("secret: secret-value-123456", encoding="utf-8")

    captured = []

    def fake_llm(body):
        captured.append(json.dumps(body, ensure_ascii=False))
        return "# 输出\nAuthorization: Bearer fake-bearer-token-1234567890\nX-Namespace: ab84335a-1248-4a9c-bbd2-a8a3048664e5"

    monkeypatch.setattr(checkpoint, "_llm_post", fake_llm)
    ctx = {
        "projects": {"测试项目"},
        "user_prompts": ["sk-testc6a2afdab413df77dfd20dc2f3970527"],
        "written_files": {str(source_doc)},
        "status": "completed",
    }

    written = checkpoint.update_project_knowledge(ctx, session_note)
    combined = "\n".join(p.read_text(encoding="utf-8") for p in written) + "\n" + "\n".join(captured)

    assert_no_secret(combined)
    assert checkpoint.REDACTION_MARKER in combined
