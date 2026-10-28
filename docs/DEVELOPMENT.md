# 开发与部署记录

> 时间：2026-10-28  
> 基线：上游 `c9b16e8`（后续 rebase 到 `29a8162`，包含 `oppo_login.py`）  
> 目标：把原本只能本机 stdio 运行的 OPPO 健康 MCP，改造成可长期运行、可远程连接、带管理面板的私有服务  
> 部署环境：腾讯云 `115.159.96.81`，Ubuntu 26.04，2 核 / 4 GB / 5 Mbps

---

## 1. 项目结构

```
cloud_mcp.py            云端签名、拉取、SQLite 缓存、MCP 工具（stdio 入口）
health_analytics.py     本地描述性统计、覆盖率、趋势、来源标记
app_server.py           Streamable HTTP MCP + 管理 API + 面板（远程入口）
dashboard.html          中文管理面板（单文件，无构建步骤）
gen_auth.py             从 root 手机经 adb 重新生成 auth.json（oppo_login.py 的备用路径）
oppo_login.py           上游新增：短信登录直接换 token，无需 root
sync_initial.py         首次批量同步脚本
verify_mcp.py           Streamable HTTP 客户端自检
Dockerfile              运行镜像（python:3.12-slim + 国内镜像源）
compose.yaml            单容器部署（host 网络、只读根文件系统、healthcheck）
deploy/nginx.conf       网关路由（/oppo-health/ → 127.0.0.1:9333）
deploy/nginx-https.snippet.conf  拿到域名后启用 443 的模板
tests/                  24 个回归测试
docs/                   部署、Agent 连接、面板统计设计
```

---

## 2. 改动清单

### 2.1 `cloud_mcp.py`

| 改动 | 原因 |
|---|---|
| `_fetch_versions()` / `_fetch_versioned_details()` / `_fetch_versioned_sport()` 返回 `http_error:<code>` | 原来 curl 失败时 `errorCode` 是 `None`，上层只看到"失败"却不知道是超时还是业务错误 |
| 版本列表为空时直接返回 `([], None)` | 原来会拿空列表继续请求明细端点，抛 `TypeError: 'NoneType' object is not iterable` |
| 新增 `_stat_window()`：7 天分窗 + `23103` 二分 + 分页游标 | 统计端点对窗口敏感，超过 7 天直接 `23103`；二分能在服务端限制变化时自适应 |
| `_fetch_versioned_stats()` 增加 `0/0` 游标兜底 | 部分老端点只认服务端默认游标，带时间窗一律 `23103` |
| `_db()` 启用 WAL + `busy_timeout=30000` | 同步写、面板读并发时避免 `database is locked` |
| 数据路径支持 `OPPO_HEALTH_DATA_DIR` / `_AUTH_FILE` / `_CACHE_DB` | 容器里代码只读，数据必须挂到 `/data` |
| 新增 `sync_log` 表 + `_log_sync()` + `sync_history()` | 面板要显示"最近一次同步成功/失败、耗时、写入条数" |
| `cloud_sync` 拆成薄包装 + `_cloud_sync_impl` | 统一记录成功/失败/异常，不侵入原有分支逻辑 |
| `cloud_status()` 增加 `last_sync` | 让 Agent 一次调用就知道每个指标最近同步情况 |
| 旧文本分析改名 `_legacy_cloud_analyze` | 新 `cloud_analyze` 返回结构化、带 `provenance` 的结果，避免同名注册两个工具 |

### 2.2 `health_analytics.py`（新增）

- 步数：按 `startTimestamp` 去重、重叠区间并集后求和，避免分段重复计数。
- 睡眠：用 `sleep_status` 阶段段自算，`sleepState` 1=清醒 / 2=深睡 / 3=REM / 4=浅睡 / 5=小睡；小睡不计入总睡眠；无清醒段时睡眠效率置 `null`（否则会出现 337/337 = 100% 的假效率）。
- 心率 / 血氧 / HRV / 压力：只做分布（min / median / max / 样本数），不给"正常/异常"判断。
- 覆盖率：每个数据集的记录数、覆盖天数、最后日期、滞后天数。
- 每个数字都带 `provenance`：`raw` / `vendor_summary` / `local_derived`。

### 2.3 `app_server.py`（新增）

- `FastMCP(streamable_http_path="/mcp", stateless_http=True, json_response=True)`。
- `TransportSecuritySettings.allowed_hosts` 从 `OPPO_HEALTH_PUBLIC_HOSTS` 读取。
- 4 个工具全部 `async` + `anyio.to_thread.run_sync`，同步操作共用一把 `anyio.Lock`。
- `BearerTokenMiddleware` 手写 ASGI 中间件（不缓冲流式响应），保护 `/mcp` 和 `/api/*`。
- `/healthz` 匿名；`/` 返回面板 HTML；`/api/summary|status|query|sync`。
- 模块导入时不强制要求 Token（否则测试无法 import），只在直接运行时检查。

### 2.4 `dashboard.html`（新增）

- 单文件，无构建、无外部依赖；Token 存 `sessionStorage`，关标签页即失效。
- 快照 / 趋势 / 缓存覆盖 / 最近同步四块；每个数字带来源标签。
- 只有 1 个数据日期时显示「样本不足，暂不绘制趋势线」，不画假曲线。
- 同步记录区分「成功」与「成功（无新数据）」。

---

## 3. 关键设计取舍

| 决策 | 理由 |
|---|---|
| 保留 stdio 入口，另加 `app_server.py` | 不破坏上游本机用法；远程和本地共用同一套业务逻辑 |
| 一个 ASGI 进程承载 MCP + API + 面板 | 2 核 4 GB 上少一个进程、少一套配置 |
| 复用已有 80 端口网关的 `/oppo-health/` 路径 | 不动安全组、不新增公网端口、不影响已有服务 |
| 容器只监听 `127.0.0.1:9333` | 公网只能经 Nginx，减少暴露面 |
| 单 worker | SQLite 单写者，多 worker 会互相锁 |
| 面板 HTML 可匿名加载，数据必须 Token | 页面本身不含数据；避免把 Token 写进源码 |
| 统计只做描述性分析 | 医疗化风险：厂商评分原样转述，本地只做求和/时长/中位数 |

---

## 4. 开发方式

- 每个修复先写失败测试，确认 RED，再改实现到 GREEN，最后跑全量回归。
- 例：`23103` 分窗、`0/0` 兜底、同步日志、`gen_auth` 解析，都有对应测试。
- 当前 `pytest tests -q` → **24 passed**。
- 真实链路验证不靠 mock：在服务器容器里对 OPPO 云端实跑 `steps_detail` / `steps_stat` / `heart_rate_stat`。

---

## 5. 开发中遇到的问题与解决

### 5.1 云端接口

| 问题 | 现象 | 解决 |
|---|---|---|
| HTTP 错误被吞掉 | `_fetch_versions` 失败时 `error` 是 `None`，上层无法区分超时和业务错误 | 统一返回 `http_error:<curl exit code>` |
| 空版本列表仍请求明细 | `TypeError: 'NoneType' object is not iterable` | 版本为空直接 `return [], None` |
| 统计窗口超限 | 请求 > 7 天返回 `23103` | `_stat_window()` 按 7 天切分；仍报错则二分窗口，最小 24 小时 |
| 老端点只认默认游标 | 带时间窗一律 `23103` | 兜底发 `start=0, end=0`，命中后给记录打 `source: zero_cursor_fallback` |
| 运动接口错误丢失 | `_fetch_versioned_sport` 返回 `None` | 同样改成 `http_error:<code>` |
| "步骤同步失败"其实是空结果 | `steps_stat` 近 7 天 `rows_cached: 0`、`error: null` | 这是 `errorCode 0 + 空列表`，不是失败；面板文案区分「成功（无新数据）」 |

### 5.2 服务与并发

| 问题 | 现象 | 解决 |
|---|---|---|
| 同步阻塞事件循环 | 直接注册同步函数，一次同步期间整个 MCP 无响应 | 全部 `async def` + `anyio.to_thread.run_sync` |
| 并发同步抢 SQLite | 潜在 `database is locked` | `anyio.Lock` 串行化 + WAL + `busy_timeout=30000` |
| 反代后 421 | `Invalid Host header` | `allowed_hosts` 加入公网 IP/域名（`OPPO_HEALTH_PUBLIC_HOSTS`） |
| `Mount("/mcp")` 触发 307 | 客户端跟随重定向后协议错乱 | 用 `streamable_http_path="/mcp"`，不挂子应用 |
| 导入即要求 Token | 测试 import 就退出 | 只有设置环境变量时才创建全局 `app` |
| 工具重名 | 新旧 `cloud_analyze` 冲突 | 旧实现改名 `_legacy_cloud_analyze`，不再注册 |

### 5.3 认证

| 问题 | 现象 | 解决 |
|---|---|---|
| 账号文件格式变了 | 6.7.17 是单行 Java 转义 JSON（`\:`、`\"`），旧解析全失败 | 先反转义再 `json.loads`，同时兼容"每行一个键"的旧格式 |
| 步数接口不再返回 `ssoid` | 当天无记录时 `resultList: []` | 改从 SensorsData `super_properties` 读 `ssoid` |
| 加密存储读不出 | `files/mmkv/*user_ssoid`、`cipher_open_usercenter.db` 都是密文 | 放弃，不走这条路 |
| 续期需要 root | `gen_auth.py` 依赖 adb + su | 上游新增 `oppo_login.py`（短信登录），优先用它；`gen_auth.py` 作为备用 |

### 5.4 面板

| 问题 | 现象 | 解决 |
|---|---|---|
| 单点趋势图空白 | 只有 1 个数据日期时画不出线，图区一片空白且无标注 | 单点状态显示数值 + 「样本不足，暂不绘制趋势线」 |
| 同步记录缺失 | 面板看不到"上次同步成功没" | 新增 `sync_log` 表 + `/api/status.sync_history` + 面板表格 |
| 空结果被当成失败 | 用户以为同步坏了 | 文案区分「成功」/「成功（无新数据）」/「失败」 |

---

## 6. 部署中遇到的问题与解决

| 问题 | 现象 | 解决 |
|---|---|---|
| Docker 构建拉不到包 | `Unable to connect to deb.debian.org`，实测 18.7 kB/s，8 分钟后失败 | Dockerfile 把 Debian 源换成 `mirrors.cloud.tencent.com`，PyPI 换成 `pypi.tuna.tsinghua.edu.cn` |
| 前台构建超时 | 单次 `docker compose build` 超过工具 420 秒上限 | 改后台进程 + 完成通知 |
| `install -d -o 10002` 报错 | `install: invalid user: '10002'` | 先 `install -d`，再 `chown 10002:10002` |
| 网关返回 401 | `/oppo-health/healthz` 被 server 块的 `auth_basic` 继承 | `location /oppo-health/` 内显式 `auth_basic off` |
| Basic Auth 与 Bearer 冲突 | 同一个 `Authorization` 头不能既放 Basic 又放 Bearer | 面板/MCP 路径关闭 Basic，改由应用层 Bearer 保护 |
| 改了页面但线上没变 | `docker compose up -d` 不重建镜像 | `dashboard.html` 等是 `COPY` 进镜像的，必须 `up -d --build` |
| Windows 下 `curl -o /dev/null` 报错 | `client returned ERROR on write` | Git Bash 用 `-o NUL` |
| 浏览器工具打不开纯 HTTP IP | 落到 `chrome-error://chromewebdata/` | 改用本机 Playwright 直接访问并注入 Token |
| 截图需要登录态 | 面板有 Token 墙 | `addInitScript` 往 `sessionStorage` 写 Token 后再导航 |
| `docker exec python -c` 引号被吃 | 远程 shell 解析后语法错误 | 把探针写成文件放进 `/data`，`docker exec python /data/xxx.py`，跑完删除 |
| 探针 import 失败 | `ModuleNotFoundError: No module named 'cloud_mcp'` | 探针里 `sys.path.insert(0, "/app")` |
| 仓库处于 detached HEAD | 无法直接提交 | 新建分支 `feat/remote-mcp-deploy` |
| 提交缺身份 | `Committer identity unknown` | 设置仓库级 `user.name` / `user.email`（不动全局） |
| 分支落后上游 | 上游已有 `29a8162`（新增 `oppo_login.py`） | `git rebase origin/master`，rebase 后 24 个测试仍全过 |
| 无推送权限 | 当前 GitHub 账号对上游仓库只有 READ | 需要 fork 到自己的账号再推分支 |

---

## 7. 实测数据（2026-10-28）

```
本地测试                     24 passed
steps_detail 7 天            23 条
steps_stat 120 天            137 条记录 / 55 个日期
缓存总量                     11 个数据集 / 11,353 条
healthz                      200
面板                         200
/api/status 无 Token         401
/api/status 错误 Token       401（MCP 路径）
/api/status 正确 Token       200
MCP 初始化                   成功，4 个工具
cloud_status                 成功
容器重启                     ~6 秒恢复 healthy
面板 1440×900 / 390×844      无横向溢出、无文字截断、无控制台报错
```

---

## 8. 后续可做

1. 域名 + 443（模板已备好，见 `deploy/nginx-https.snippet.conf`）。
2. 把 `docs/panel-stats-design.md` 里未实现的指标补齐：缺口热力图、跨源一致性校验、厂商报告折叠区。
3. 定时同步（cron / systemd timer），现在只有手动和 API 触发。
4. 同步失败告警（Telegram / 邮件）。
5. 多用户支持（当前是单账号、单 Token）。
