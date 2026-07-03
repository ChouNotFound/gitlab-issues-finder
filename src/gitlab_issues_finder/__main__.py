"""支持 `python -m gitlab_issues_finder` 启动 Web 服务。"""

import uvicorn

from gitlab_issues_finder.config import AppConfig


def main() -> None:
    cfg = AppConfig.from_env()
    uvicorn.run(
        "gitlab_issues_finder.app:app",
        host=cfg.web_host,
        port=cfg.web_port,
        reload=False,
    )


if __name__ == "__main__":
    main()