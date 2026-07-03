# GitLab Status Board

个人工具：在公司自托管 GitLab 上，打开一个用户名，**一目了然**展示你的 issue / merge request。

不止是查询工具。它是 **Git 状态看板** — 按"与我关系"分桶，5 列 Kanban 视图，拖拽改列、改列名、新增自定义列、删除自定义列、Light/Dark/Auto 主题切换、最近用户快捷入口。所有看板改动跨刷新保留（SQLite 持久化）。

## ✨ 特性

- 🔍 **多维筛选**：assignee / mention / author 三维度（MR 额外 reviewer） 同时拉取，去重合并
- 🏷️ **自选标签**：逗号分隔多标签，AND 关系精确过滤
- 🗂️ **看板视图**：5 列 Kanban + 自定义列、拖拽改分桶、列名可改、列可删可加
- 🎨 **现代 UI**：仿 Linear / GitLab / Trello 设计风格，CSS 变量主题切换 (Light/Dark/Auto)
- 💾 **本地持久化**：SQLite 存看板状态、用户偏好、最近访问用户
- 🧪 **完整测试**：88 个单元测试，覆盖配置、查询、客户端、Web 路由、看板 API、存储层

## 🚀 快速开始

### 1. 安装

```bash
cd gitlab-issues-finder
python -m venv .venv
source .venv/Scripts/activate   # Windows Git Bash
pip install -r requirements.txt
pip install -e .                # 本地安装包
```

### 2. 配置 `.env`

```bash
cp .env.example .env
```

编辑 `.env`：

```dotenv
GITLAB_URL=https://gitlab.your-company.com
GITLAB_TOKEN=glpat-xxxxxxxxxxxxxxxxxxxx   # scope: read_api
```

### 3. 启动

```bash
uvicorn gitlab_issues_finder.app:app --host 127.0.0.1 --port 8000
```

浏览器打开 <http://127.0.0.1:8000>

## 📸 视图概览

### 首页（`/`）

干净的卡片式入口，输入 GitLab 用户名直达看板。下方展示最近 8 个访问的用户（存在 SQLite），点击 chip 即可快速跳转。

### 查询视图（`/search`）

按 type 分两段（Issues、Merge Requests），列出所有参与维度的命中项。

每行展示：
- IID + 所属项目（`#42 p101`）
- 标题（点击跳到 GitLab）
- 状态徽章
- **命中维度标签**：assignee / mention / author / reviewer / label
- Labels / Assignee / Updated

### 看板视图（`/board?username=X`）

**5 列默认**：

| 列 | 命中条件 |
|---|---|
| 需我审查 | `reviewer_username=X` |
| 需我动 | `assignee_username=X` |
| @我的 | `mention_username=X` |
| 我创建的 | `author_username=X` |
| 其他参与 | 兜底（未匹配上述任一维度） |

**特性**：
- 拖拽卡片到任意列 → 立即持久化到 SQLite
- 列名可编辑（内置列 readonly，自定义列可重命名）
- 添加自定义列 `+ 新列`
- 删除自定义列（悬停 hover 出删除按钮）
- 重置所有拖拽 → 一键回到默认分桶
- 标签过滤：顶部搜索框按 comma-separated labels 过滤
- 主题切换：右上角 ◐ 按钮循环 Light → Dark → Auto

## 🔌 API

| Method | Path | 说明 |
|---|---|---|
| `GET` | `/` | 首页 |
| `POST` | `/search` | 查询视图 |
| `GET` | `/board` | 看板视图 |
| `GET` | `/api/users` | 活跃用户列表（首页自动补全） |
| `GET` | `/api/recent-users` | 最近访问的用户 |
| `POST` | `/api/board/move` | 拖拽覆盖 `{username, item_key, column_id}` |
| `POST` | `/api/board/reset` | 清除某用户所有拖拽 |
| `POST` | `/api/board/columns` | 新增列 `{username, column_id, title}` |
| `PATCH` | `/api/board/columns/{cid}` | 重命名列 |
| `DELETE` | `/api/board/columns/{cid}` | 删除自定义列 |
| `POST` | `/api/preferences` | 主题 `{username, theme}` |
| GET  | /api/version | 应用 + Python + FastAPI 版本 |
| GET  | /api/health | 健康检查（DB + config） |

> 拖拽与列管理**仅本地**，不调任何 mutation API；看板始终只需 `read_api` Token。

## ⚙️ 配置项

| 环境变量 | 必填 | 默认值 | 说明 |
|---|---|---|---|
| `GITLAB_URL` | ✅ | — | GitLab 实例 URL |
| `GITLAB_TOKEN` | ✅ | — | Personal Access Token（scope: `read_api`） |
| `GITLAB_SSL_VERIFY` | | `true` | `true` / `false` / CA bundle 路径 |
| `GITLAB_TIMEOUT` | | `30` | HTTP 超时（秒） |
| `WEB_HOST` | | `127.0.0.1` | Web 监听 |
| `WEB_PORT` | | `8000` | Web 端口 |
| `PAGE_SIZE` | | `100` | GitLab API 每页（1-100） |
| `DB_PATH` | | `data/app.db` | SQLite 文件位置 |

## 🔐 SSL 自签名证书

```dotenv
GITLAB_SSL_VERIFY=false                 # 临时方案
# 或
GITLAB_SSL_VERIFY=C:/certs/ca-bundle.crt  # 推荐
```

## 🧱 项目结构

```
gitlab-issues-finder/
├── pyproject.toml
├── requirements.txt
├── requirements-dev.txt
├── .env.example
├── src/gitlab_issues_finder/
│   ├── app.py           # FastAPI 路由 (/, /search, /board) + JSON API
│   ├── client.py        # GitLab 客户端 + 异常映射
│   ├── config.py        # .env 加载
│   ├── errors.py        # 异常层级
│   ├── models.py        # ItemRef (issue + MR 共用)
│   ├── queries.py       # 多维度查询 (assignee/mention/author/reviewer/labels) + 分页 + 去重
│   ├── storage.py       # SQLite 持久化层 (看板、列、主题、最近用户)
│   ├── templates/
│   │   ├── _nav.html    # 顶部 nav + 主题切换器
│   │   ├── index.html   # 首页：用户名输入
│   │   ├── result.html  # 查询结果（双表格）
│   │   ├── board.html   # 5 列 Kanban + 拖拽
│   │   └── error.html   # 错误页
│   └── static/style.css # 全局 CSS，含 light/dark/auto 三套主题变量
└── tests/
    ├── conftest.py          # 共享 fixture + tmp_db 自动 init schema
    ├── test_config.py
    ├── test_client.py
    ├── test_models.py
    ├── test_queries.py
    ├── test_storage.py      # 看板持久化单元测试
    ├── test_app.py          # 端到端 + 看板 API
    └── fixtures/
```

## ✅ 运行测试

```bash
pip install -r requirements-dev.txt
pytest -v
```

测试覆盖（88 个）：
- `test_config.py`：配置加载、SSL 解析、缺失必填项、`DB_PATH`
- `test_models.py`：`ItemRef` 数据类、`type` 字段、三元组 key
- `test_queries.py`：分页、单页/多页、空结果、跨类型不去重、各维度参数路由
- `test_client.py`：异常映射（401/403/500/超时）、SSL
- `test_app.py`：FastAPI 端到端、错误页渲染、看板 5 列分桶、列管理 API、主题切换
- `test_storage.py`：看板拖拽、列 CRUD、主题、最近用户

## 🐳 Docker 部署

项目根目录已带 `Dockerfile`（多阶段构建：builder + 3.12-slim runtime）
与 `docker-compose.yml`。

`ash
cp .env.docker.example .env       # 填入 GITLAB_URL / GITLAB_TOKEN
docker compose up -d --build
`

数据持久化：SQLite 落盘在 `app-data` volume（默认 `/app/data/app.db`），
重启 / 升级容器不丢看板状态。

健康检查：`HEALTHCHECK` 每 30s 探一次 `/api/health`。

镜像以非 root 用户（`app`）运行，仅暴露 `8000` 端口。

## 🐛 常见问题

**Q: 启动报 `GITLAB_TOKEN` / `GITLAB_URL` 未设置**
A: 检查 `.env` 是否在项目根目录、文件名不是 `.env.txt`。

**Q: 401 Unauthorized**
A:
1. 检查 token 拼写
2. 是否过期
3. 是否勾选 `read_api` scope

**Q: SSL 证书错误**
A: 见 [SSL 自签名](#-ssl-自签名证书)。

**Q: 看板显示"未找到"**
A:
1. 用户名拼写区分大小写
2. Token 是否有目标 project 的 read 权限
3. GitLab 实例是否支持 `mention_username` 参数（GitLab ≥ 12.0）

**Q: 拖拽改列后看不到了**
A: 顶部点"重置拖拽"恢复默认分桶。

## 🗺️ 后续可能扩展

- 缓存层（requests-cache）
- Group 范围过滤
- 关键词搜索
- 导出 CSV / Markdown
- 看板拖拽同步到 GitLab（需升级 Token scope 到 `api`）
- 个人仪表盘（项目健康度、最近活动时间线）

## 📄 License

个人工具，仅供内部使用。
