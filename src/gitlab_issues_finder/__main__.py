"""CLI 入口: `python -m gitlab_issues_finder [--version]`。

启动 uvicorn 之前先校验配置: 缺失/非法 .env 给出明确文案并退出码 2,
而不是 stacktrace, 让用户能直接看到要修什么。
"""

import sys

import uvicorn

from gitlab_issues_finder import __version__
from gitlab_issues_finder.config import AppConfig
from gitlab_issues_finder.errors import ConfigError


def main() -> None:
    if "--version" in sys.argv or "-V" in sys.argv:
        print(f"gitlab-issues-finder {__version__}")
        return
    try:
        cfg = AppConfig.from_env()
    except ConfigError as e:
        # 启动时配置错误: 清晰文案 + 修复建议 + 退出码 2
        print(f"配置错误: {e}", file=sys.stderr)
        hint = getattr(e, "hint", None)
        if hint:
            print(f"修复: {hint}", file=sys.stderr)
        else:
            print(
                "提示: 复制 .env.example 为 .env 并填写 GITLAB_URL / GITLAB_TOKEN。",
                file=sys.stderr,
            )
        sys.exit(2)
    uvicorn.run(
        "gitlab_issues_finder.app:app",
        host=cfg.web_host,
        port=cfg.web_port,
        reload=False,
    )


if __name__ == "__main__":
    main()
