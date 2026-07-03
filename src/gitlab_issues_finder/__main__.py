"""支持 `python -m gitlab_issues_finder` 启动 Web 服务。

可选参数：
  - --version / -V        输出包版本后退出
  - 无参数            启动 uvicorn 服务器（默认从 .env 读 WEB_HOST / WEB_PORT）
"""

import sys

import uvicorn

from gitlab_issues_finder import __version__
from gitlab_issues_finder.config import AppConfig


def main() -> None:
    if "--version" in sys.argv or "-V" in sys.argv:
        print(f"gitlab-issues-finder {__version__}")
        return
    cfg = AppConfig.from_env()
    uvicorn.run(
        "gitlab_issues_finder.app:app",
        host=cfg.web_host,
        port=cfg.web_port,
        reload=False,
    )


if __name__ == "__main__":
    main()
