# Tech Stack Decision

评估日期: 2026-07-04
适用版本: gitlab-issues-finder >= 0.2.0

## 当前栈

| 组件 | 选型 | 用途 | 来源 |
|---|---|---|---|
| Web 框架 | FastAPI 0.110+ | 路由 / 依赖注入 / OpenAPI | pyproject |
| ASGI 服务器 | uvicorn[standard] | 生产 / 开发服务 | pyproject |
| 模板 | Jinja2 3.1+ | SSR HTML 页面 | pyproject |
| GitLab SDK | python-gitlab 4.x | HTTP 客户端 + 异常类 | pyproject |
| 配置 | pydantic-settings | 类型化 env | pyproject |
| 持久化 | SQLite (stdlib) | 看板状态 / 用户偏好 | stdlib |
| HTTP 测试 mock | responses | 拦截 requests | requirements-dev |
| 前端 | Vanilla JS (66 行) + CSS (1300 行) | 主题 / 拖拽 / 加载 | 自研 |

代码量: 2.5K Python + 0.9K HTML + 1.3K CSS + 0.07K JS = ~4.8K 行
测试量: 3.5K 行 / 250 用例
依赖总数 (含传递): ~30 个 wheel 包

## 评估矩阵

### python-gitlab (RECOMMEND: 替换为 `requests`)

**当前用法**: 仅 `gl.http_get(path, query_data=...)` + `gitlab.Gitlab(url, token)` 构造。
未使用 python-gitlab 的高阶 Project / Issue 对象管理器 (我们的 queries.py
都是手写 REST 调用)。

| 维度 | python-gitlab | requests 直接 |
|---|---|---|
| 依赖大小 | ~1.5 MB wheel | 0 (已作为 python-gitlab 依赖装好) |
| Python 3.14 兼容 | 4.13 在 3.14 上有 RESTObject 初始化 bug, 需绕过 | 完美 |
| 异常处理 | 需手动映射, 默认还会 sleep+retry 数十秒 | 直接捕获, 行为可控 |
| 已知风险 | 默认 `obey_rate_limit=True` + `max_retries=10`, 429 阻塞 30s+ | 默认即"fail fast" |
| 测试 mock | 已用 `responses` 库 (python-gitlab 走 requests) | 同 `responses` |
| 代码可读性 | 多一层"为什么这里调 http_get"的心智负担 | 直接 `requests.get(...)` |
| 社区活跃度 | 中 (企业级 SDK) | 极高 (de-facto 标准) |
| 破坏性 | -- | 需改 ~30 行 client.py, 250 测试 |

**结论**: 替换。当前 90% 的 python-gitlab 价值 (类型化对象) 我们用不到,
剩下的 10% (HTTP 客户端) `requests` 完全等价 + 更可控。`safe_http_get`
的 9 个异常类型映射保持不变, 250 测试零改动。

### Jinja2 (RECOMMEND: 保留)

替代方案: HTMX + Jinja2 partial, htmx + Alpine, LiveView-like, server components。

| 维度 | 当前 | HTMX | server components |
|---|---|---|---|
| 学习成本 | 低 (模板) | 中 (新语法) | 高 (新架构) |
| 客户端依赖 | 66 行 vanilla JS | ~14KB htmx | 框架自带 runtime |
| 渐进式 | 是 (无 JS 也可用) | 是 | 否 |
| 当前需求匹配 | 看板上, 主要操作: 整页跳转 / 拖拽 / form 提交 | 看板局部刷新价值不大 | 过重 |

**结论**: 保留。当前应用的信息密度和交互模式与 Jinja2 完美匹配; 引入
HTMX 反而要为"看板拖拽"等需要双向状态同步的复杂交互付出不成比例的成本。

### SQLite (RECOMMEND: 保留)

替代方案: JSON 文件 / TinyDB / Postgres。

| 维度 | 当前 | JSON | Postgres |
|---|---|---|---|
| 部署复杂度 | 0 (单文件) | 0 | 高 |
| 并发 | 够用 (个人工具) | 弱 (写锁全文件) | 强 |
| 查询能力 | 强 (SQL) | 弱 (内存过滤) | 强 |
| 索引支持 | 是 (本次迭代加 last_seen) | 否 | 是 |
| 适用规模 | 千级行足够 | 10 行 | 百万级 |

**结论**: 保留。SQLite 已是个人工具的"最大公约数", 引入 Postgres 反而
让 docker-compose 部署从 0 步变 5 步。

### uvicorn (RECOMMEND: 保留)

替代方案: hypercorn, granian, gunicorn + uvicorn workers。

**结论**: 保留。当前并发量 (个人使用) uvicorn 单进程足矣; 必要时再前置
gunicorn, 不必现在就引入。

## 决策

1. **本次迭代**: python-gitlab → requests (删除 python-gitlab 依赖)。
2. **暂不动**: Jinja2 / SQLite / uvicorn, 均匹配当前规模与需求。
3. **未来观察**: 若看板扩展到多用户 / 团队场景, 重新评估 SQLite → Postgres。
