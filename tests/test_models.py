"""models.py 单元测试。"""

from __future__ import annotations

from gitlab_issues_finder.models import IssueRef


def make_payload(**overrides) -> dict:
    base = {
        "project_id": 101,
        "iid": 1,
        "title": "Test",
        "state": "opened",
        "labels": ["bug"],
        "assignee": {"username": "alice"},
        "web_url": "https://gl/x",
        "updated_at": "2026-07-01T00:00:00Z",
    }
    base.update(overrides)
    return base


class TestIssueRefFromApi:
    def test_basic(self):
        issue = IssueRef.from_api(make_payload())
        assert issue.type == "issue"
        assert issue.project_id == 101
        assert issue.iid == 1
        assert issue.title == "Test"
        assert issue.state == "opened"
        assert issue.labels == ("bug",)
        assert issue.assignee == "alice"
        assert issue.web_url == "https://gl/x"
        assert issue.updated_at == "2026-07-01T00:00:00Z"

    def test_no_assignee(self):
        issue = IssueRef.from_api(make_payload(assignee=None))
        assert issue.assignee is None

    def test_empty_labels(self):
        issue = IssueRef.from_api(make_payload(labels=[]))
        assert issue.labels == ()

    def test_missing_labels(self):
        payload = make_payload()
        payload.pop("labels")
        issue = IssueRef.from_api(payload)
        assert issue.labels == ()

    def test_default_type_is_issue(self):
        """不传 type 应默认为 "issue"。"""
        issue = IssueRef.from_api(make_payload())
        assert issue.type == "issue"

    def test_override_type_merge_request(self):
        """type 关键字参数允许覆盖为 merge_request。"""
        mr = IssueRef.from_api(make_payload(), type="merge_request")
        assert mr.type == "merge_request"

    def test_key_includes_type(self):
        issue = IssueRef.from_api(make_payload(project_id=42, iid=7))
        assert issue.key == ("issue", 42, 7)

        mr = IssueRef.from_api(make_payload(project_id=42, iid=7), type="merge_request")
        assert mr.key == ("merge_request", 42, 7)

    def test_key_distinguishes_cross_type_same_iid(self):
        """同 (project_id, iid) 的 issue 与 MR 必须有不同 key。"""
        issue = IssueRef.from_api(make_payload(iid=5), type="issue")
        mr = IssueRef.from_api(make_payload(iid=5), type="merge_request")
        assert issue.key != mr.key
        # 显式检查类型维度
        assert issue.key[0] == "issue"
        assert mr.key[0] == "merge_request"

    def test_frozen(self):
        issue = IssueRef.from_api(make_payload())
        with __import__("pytest").raises(Exception):
            issue.title = "new"  # type: ignore[misc]
