"""storage.py 单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from gitlab_issues_finder import storage


class TestOverride:
    def test_set_and_get(self, tmp_db):
        storage.set_override(tmp_db, "alice", "merge_request-1-1", "reviewer")
        storage.set_override(tmp_db, "alice", "issue-2-3", "assignee")
        assert storage.get_overrides(tmp_db, "alice") == {
            "merge_request-1-1": "reviewer",
            "issue-2-3": "assignee",
        }

    def test_update_existing(self, tmp_db):
        storage.set_override(tmp_db, "alice", "k", "reviewer")
        storage.set_override(tmp_db, "alice", "k", "assignee")
        assert storage.get_overrides(tmp_db, "alice") == {"k": "assignee"}

    def test_clear(self, tmp_db):
        storage.set_override(tmp_db, "alice", "k", "reviewer")
        storage.clear_overrides(tmp_db, "alice")
        assert storage.get_overrides(tmp_db, "alice") == {}

    def test_isolation_by_user(self, tmp_db):
        storage.set_override(tmp_db, "alice", "k", "reviewer")
        storage.set_override(tmp_db, "bob", "k", "assignee")
        assert storage.get_overrides(tmp_db, "alice") == {"k": "reviewer"}
        assert storage.get_overrides(tmp_db, "bob") == {"k": "assignee"}


class TestColumns:
    def test_first_call_initializes_builtin(self, tmp_db):
        cols = storage.list_columns(tmp_db, "alice")
        ids = [c["id"] for c in cols]
        assert ids == ["reviewer", "assignee", "mention", "author", "other"]
        assert all(c["is_builtin"] for c in cols)

    def test_add_custom_column(self, tmp_db):
        storage.list_columns(tmp_db, "alice")
        c = storage.add_column(tmp_db, "alice", "reviewing", "待 review")
        assert c["id"] == "reviewing"
        assert c["is_builtin"] is False
        cols = storage.list_columns(tmp_db, "alice")
        # 自定义列追加在末尾
        assert cols[-1]["id"] == "reviewing"

    def test_rename_column(self, tmp_db):
        storage.list_columns(tmp_db, "alice")
        storage.add_column(tmp_db, "alice", "reviewing", "待 review")
        ok = storage.rename_column(tmp_db, "alice", "reviewing", "需要 review")
        assert ok is True
        cols = storage.list_columns(tmp_db, "alice")
        renamed = [c for c in cols if c["id"] == "reviewing"][0]
        assert renamed["title"] == "需要 review"

    def test_delete_custom_column(self, tmp_db):
        storage.list_columns(tmp_db, "alice")
        storage.add_column(tmp_db, "alice", "reviewing", "待 review")
        ok = storage.delete_column(tmp_db, "alice", "reviewing")
        assert ok is True
        cols = [c["id"] for c in storage.list_columns(tmp_db, "alice")]
        assert "reviewing" not in cols

    def test_delete_builtin_column_returns_false(self, tmp_db):
        storage.list_columns(tmp_db, "alice")
        assert storage.delete_column(tmp_db, "alice", "reviewer") is False

    def test_delete_missing_column_returns_false(self, tmp_db):
        storage.list_columns(tmp_db, "alice")
        assert storage.delete_column(tmp_db, "alice", "nonexistent") is False

    def test_rename_missing_returns_false(self, tmp_db):
        storage.list_columns(tmp_db, "alice")
        assert storage.rename_column(tmp_db, "alice", "nonexistent", "x") is False


class TestPreferences:
    def test_default_theme_auto(self, tmp_db):
        assert storage.get_theme(tmp_db, "alice") == "auto"

    def test_set_theme(self, tmp_db):
        storage.set_theme(tmp_db, "alice", "dark")
        assert storage.get_theme(tmp_db, "alice") == "dark"
        storage.set_theme(tmp_db, "alice", "light")
        assert storage.get_theme(tmp_db, "alice") == "light"

    def test_invalid_theme_rejected(self, tmp_db):
        with pytest.raises(ValueError):
            storage.set_theme(tmp_db, "alice", "rainbow")

    def test_recent_users_order(self, tmp_db):
        storage.touch_user(tmp_db, "alice")
        storage.touch_user(tmp_db, "bob")
        recent = storage.list_recent_users(tmp_db, limit=10)
        # 后 touch 的排前
        assert recent[0] == "bob"
        assert recent[1] == "alice"


class TestInit:
    def test_init_creates_schema(self, tmp_path: Path):
        from gitlab_issues_finder.storage import _connect

        dbp = tmp_path / "x.db"
        storage.init_db(dbp)
        # 验证表已创建
        with _connect(dbp) as conn:
            tables = {
                r["name"]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        assert {
            "board_overrides",
            "board_columns",
            "user_prefs",
        }.issubset(tables)

    def test_init_idempotent(self, tmp_db):
        storage.init_db(tmp_db)
        storage.init_db(tmp_db)  # 不应抛错


class TestReorderColumnsStorage:
    def test_reorder_assigns_zero_based_indices(self, tmp_db):
        from gitlab_issues_finder import storage

        storage.list_columns(tmp_db, "alice")  # initializes builtins
        new_order = ["author", "reviewer", "assignee", "mention", "other"]
        updated = storage.reorder_columns(tmp_db, "alice", new_order)
        assert updated == 5
        cols = storage.list_columns(tmp_db, "alice")
        assert [c["id"] for c in cols] == new_order
        # sort_index is 0..4
        assert [c["sort_index"] for c in cols] == [0, 1, 2, 3, 4]

    def test_reorder_partial_only_updates_known(self, tmp_db):
        from gitlab_issues_finder import storage

        storage.list_columns(tmp_db, "alice")
        updated = storage.reorder_columns(tmp_db, "alice", ["author", "nope"])
        # Only 'author' exists in builtins
        assert updated == 1
        cols = storage.list_columns(tmp_db, "alice")
        # 'author' now has sort_index=0; others keep their original higher indices
        assert cols[0]["id"] == "author"
        assert cols[0]["sort_index"] == 0
