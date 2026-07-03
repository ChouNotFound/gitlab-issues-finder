"""展示用数据模型。

`IssueRef` 是 issue / merge request 共用的精简引用（用于列表展示）。
GitLab 同 project 内 issue 与 merge request 共享 iid 序列，因此全局唯一键
必须包含类型：(type, project_id, iid)。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ItemType = Literal["issue", "merge_request"]


@dataclass(frozen=True)
class IssueRef:
    """一个 issue 或 merge request 的精简引用（用于列表展示）。"""

    type: str  # "issue" | "merge_request"
    project_id: int
    iid: int
    title: str
    state: str
    labels: tuple[str, ...]
    assignee: str | None
    web_url: str
    updated_at: str  # ISO 8601 字符串

    @property
    def key(self) -> tuple[str, int, int]:
        """全局唯一键：跨类型区分 type 防止 (project_id, iid) 冲突。"""
        return (self.type, self.project_id, self.iid)

    @classmethod
    def from_api(cls, payload: dict, *, type: str = "issue") -> "IssueRef":
        """从 GitLab API 返回的 issue / merge_request dict 构造。

        GitLab issue 与 merge request API 返回字段结构高度一致，
        复用此构造器，仅靠 type= 参数区分。
        """
        assignee_payload = payload.get("assignee") or {}
        return cls(
            type=type,
            project_id=int(payload["project_id"]),
            iid=int(payload["iid"]),
            title=str(payload.get("title", "")),
            state=str(payload.get("state", "")),
            labels=tuple(payload.get("labels") or []),
            assignee=assignee_payload.get("username"),
            web_url=str(payload.get("web_url", "")),
            updated_at=str(payload.get("updated_at", "")),
        )
