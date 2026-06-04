"""Tests for SOUL.md fenced-block upsert / clear."""

from __future__ import annotations

from pathlib import Path

from colony_chat_hermes import soul_anchor

START = soul_anchor.DEFAULT_START_MARKER
END = soul_anchor.DEFAULT_END_MARKER


class TestBuildBlock:
    def test_includes_handle_and_landing_url(self) -> None:
        block = soul_anchor.build_block(handle="my-agent")
        assert "@my-agent" in block
        assert "chat.thecolony.cc" in block

    def test_profile_url_substitutes_handle(self) -> None:
        block = soul_anchor.build_block(handle="my-agent")
        assert "https://thecolony.cc/u/my-agent" in block

    def test_custom_landing_and_profile_url(self) -> None:
        block = soul_anchor.build_block(
            handle="x",
            landing_url="https://example.test",
            profile_url_template="https://example.test/agents/{handle}",
        )
        assert "https://example.test" in block
        assert "https://example.test/agents/x" in block


class TestUpsert:
    def test_creates_file_when_missing(self, tmp_path: Path) -> None:
        soul = tmp_path / "SOUL.md"
        soul_anchor.upsert(handle="my-agent", soul_path=soul)
        content = soul.read_text()
        assert START in content
        assert END in content
        assert "@my-agent" in content

    def test_inserts_into_existing_file(self, tmp_path: Path) -> None:
        soul = tmp_path / "SOUL.md"
        soul.write_text("# Soul\n\nI am an agent.\n")
        soul_anchor.upsert(handle="my-agent", soul_path=soul)
        content = soul.read_text()
        assert "I am an agent." in content
        assert START in content
        assert END in content

    def test_replaces_existing_block(self, tmp_path: Path) -> None:
        soul = tmp_path / "SOUL.md"
        soul_anchor.upsert(handle="old-handle", soul_path=soul)
        soul_anchor.upsert(handle="new-handle", soul_path=soul)
        content = soul.read_text()
        assert "@new-handle" in content
        assert "@old-handle" not in content
        # Block should appear exactly once.
        assert content.count(START) == 1
        assert content.count(END) == 1

    def test_idempotent_byte_for_byte(self, tmp_path: Path) -> None:
        soul = tmp_path / "SOUL.md"
        soul_anchor.upsert(handle="my-agent", soul_path=soul)
        first = soul.read_text()
        soul_anchor.upsert(handle="my-agent", soul_path=soul)
        second = soul.read_text()
        assert first == second

    def test_preserves_other_plugins_blocks(self, tmp_path: Path) -> None:
        soul = tmp_path / "SOUL.md"
        soul.write_text(
            "# Soul\n\n"
            "<!-- other-plugin:start -->\n"
            "I belong to another plugin.\n"
            "<!-- other-plugin:end -->\n"
        )
        soul_anchor.upsert(handle="my-agent", soul_path=soul)
        content = soul.read_text()
        assert "<!-- other-plugin:start -->" in content
        assert "I belong to another plugin." in content
        assert "<!-- other-plugin:end -->" in content
        assert START in content


class TestClear:
    def test_no_op_when_file_missing(self, tmp_path: Path) -> None:
        result = soul_anchor.clear(soul_path=tmp_path / "missing.md")
        assert result is False

    def test_no_op_when_block_absent(self, tmp_path: Path) -> None:
        soul = tmp_path / "SOUL.md"
        soul.write_text("# Soul\n\nNothing here.\n")
        result = soul_anchor.clear(soul_path=soul)
        assert result is False
        assert soul.read_text() == "# Soul\n\nNothing here.\n"

    def test_removes_block_when_present(self, tmp_path: Path) -> None:
        soul = tmp_path / "SOUL.md"
        soul.write_text("# Soul\n\nIntro.\n")
        soul_anchor.upsert(handle="my-agent", soul_path=soul)
        assert START in soul.read_text()
        result = soul_anchor.clear(soul_path=soul)
        assert result is True
        content = soul.read_text()
        assert START not in content
        assert END not in content
        assert "Intro." in content  # other content preserved

    def test_clear_leaves_other_plugins_alone(self, tmp_path: Path) -> None:
        soul = tmp_path / "SOUL.md"
        soul.write_text("# Soul\n\n<!-- other:start -->\nother plugin\n<!-- other:end -->\n")
        soul_anchor.upsert(handle="my-agent", soul_path=soul)
        soul_anchor.clear(soul_path=soul)
        content = soul.read_text()
        assert "<!-- other:start -->" in content
        assert "other plugin" in content
        assert START not in content
