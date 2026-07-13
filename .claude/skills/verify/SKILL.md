---
name: verify
summary: Drive checkpoint metadata flows through the CLI.
---

# Verify checkpoint metadata

1. Create a temporary vault and a minimal JSONL transcript containing a real `Write` tool event into `Claude方案/<项目>/`.
2. Run `.claude/hooks/checkpoint.py` once in lite CLI mode with topic, category, tags, and keywords.
3. Edit the generated temporary checkpoint only: clear `keywords` and set a custom `aliases` value.
4. Run the same session again without `--force` and capture CLI output plus checkpoint, project summary, and AI reference frontmatter.
5. Verify search behavior against the temporary vault: aliases first, then keywords, tags, and full-text fallback; include one missing-term probe.
6. Never point `OBSIDIAN_VAULT` at the real vault for destructive verification. Do not commit or push.
