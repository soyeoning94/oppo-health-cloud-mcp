# OPPO 健康远程 MCP —— 部署说明

> 版本：2026-10-28  
> 目标服务器：腾讯云 `115.159.96.81`（Ubuntu 26.04，2 核 / 4 GB）  
> 代码基线：`c9b16e87b0bb51ad15a3196becdb779b46d21ec2` + 本项目改动

---

## 1. 这套服务是什么

把 OPPO / 欢太健康云的个人健康数据，通过一个长期运行的服务暴露成两种接口：

| 接口 | 地址 | 用途 |
|---|---|---|
| MCP（Streamable HTTP） | `http://115.159.96.81/oppo-health/mcp` | 给 Claude / Cursor / Hermes 等 Agent 调用 |
| 管理面板 | `http://115.159.96.81/oppo-health/` | 浏览器查看健康快照、趋势、缓存覆盖、同步记录 |
| 管理 API | `http://115.159.96.81/oppo-health/api/*` | 面板使用，也可被脚本调用 |
| 健康检查 | `http://115.159.96.81/oppo-health/healthz` | 匿名，仅返回存活状态 |

MCP 工具：

| 工具 | 读写 | 说明 |
|---|---|---|
| `cloud_sync(metric, days)` | 写 | 从 OPPO 云端拉取并写入 SQLite 缓存 |
| `cloud_query(metric, date)` | 只读 | 查询缓存，`date` 支持 `YYYYMMDD` / `YYYY-MM-DD` |
| `cloud_analyze(days)` | 只读 | 带来源标签的描述性统计与数据质量 |
| `cloud_status()` | 只读 | 认证状态、各数据集条数、最近同步记录 |

---

## 2. 架构

```
浏览器 / Agent
      │  Bearer Token
      ▼
service-portal-gateway (Nginx, 80)
      │  /oppo-health/  → 去前缀
      ▼
oppo-health-mcp (Docker, 127.0.0.1:9333)
      │  FastMCP Streamable HTTP + /api/* + dashboard.html
      ▼
/data/cloud_cache.db (SQLite, WAL)
      ▲
      │  HMAC-SHA256 签名 + curl TLS 指纹
OPPO 健康云 sport.health.heytapmobi.com
```

要点：

- 复用已有 80 端口网关，不新增公网端口。
- 容器只监听 `127.0.0.1:9333`，公网不可直连。
- `/oppo-health/` 关闭了门户的 Basic Auth，改由应用层 Bearer Token 保护 MCP 与 API；面板 HTML 外壳可匿名加载，但所有真实数据必须带 Token。
- 容器以 `10002:10002` 运行，根文件系统只读，仅 `/data` 可写。

---

## 3. 服务器文件布局

```
/opt/oppo-health/
├── cloud_mcp.py            云端签名、拉取、缓存、统计入口
├── health_analytics.py     本地描述性统计与数据质量
├── app_server.py           Streamable HTTP MCP + API + 面板
├── dashboard.html          管理面板
├── gen_auth.py             从手机重新生成 auth.json（需要 adb + root）
├── requirements.txt
├── Dockerfile
├── compose.yaml
├── private/
│   └── service.env         环境变量与 Token（600 root:root）
└── data/                   容器挂载点（10002:10002，700）
    ├── auth.json           OPPO 认证三件套（600）
    └── cloud_cache.db      健康数据缓存（600）
```

Nginx 配置：`/opt/service-portal/nginx.conf`  
改动前备份：`/opt/service-portal/nginx.conf.backup-20261028T104348`

---

## 4. 环境变量

`/opt/oppo-health/private/service.env`：

```ini
OPPO_HEALTH_API_TOKEN=<32 字节随机 hex>
OPPO_HEALTH_DATA_DIR=/data
OPPO_HEALTH_AUTH_FILE=/data/auth.json
OPPO_HEALTH_CACHE_DB=/data/cloud_cache.db
OPPO_HEALTH_HOST=127.0.0.1
OPPO_HEALTH_PORT=9333
OPPO_HEALTH_PUBLIC_HOSTS=115.159.96.81
OPPO_HEALTH_LOG_LEVEL=info
TZ=Asia/Shanghai
```

- `OPPO_HEALTH_PUBLIC_HOSTS` 是 FastMCP 的 DNS rebinding 白名单；**删掉会导致反向代理返回 421**。
- 换域名后要把域名加进这个变量（逗号分隔）。
- Token 轮换：改 `service.env` 后 `docker compose up -d`，所有 Agent 配置需同步更新。

---

## 5. 首次部署（换机器时照做）

```bash
# 1. 目录与权限
install -d -m 0755 /opt/oppo-health
install -d -m 0700 /opt/oppo-health/private
install -d -m 0700 /opt/oppo-health/data
chown 10002:10002 /opt/oppo-health/data

# 2. 上传代码（在本地执行）
scp cloud_mcp.py health_analytics.py app_server.py dashboard.html gen_auth.py \
    requirements.txt Dockerfile compose.yaml root@115.159.96.81:/opt/oppo-health/

# 3. 生成 Token 与 env
umask 077
printf '%s\n' \
  "OPPO_HEALTH_API_TOKEN=$(openssl rand -hex 32)" \
  "OPPO_HEALTH_DATA_DIR=/data" \
  "OPPO_HEALTH_AUTH_FILE=/data/auth.json" \
  "OPPO_HEALTH_CACHE_DB=/data/cloud_cache.db" \
  "OPPO_HEALTH_HOST=127.0.0.1" \
  "OPPO_HEALTH_PORT=9333" \
  "OPPO_HEALTH_PUBLIC_HOSTS=115.159.96.81" \
  "TZ=Asia/Shanghai" > /opt/oppo-health/private/service.env
chmod 600 /opt/oppo-health/private/service.env

# 4. 放认证与缓存
scp auth.json cloud_cache.db root@115.159.96.81:/opt/oppo-health/data/
ssh root@115.159.96.81 'chown 10002:10002 /opt/oppo-health/data/*; chmod 600 /opt/oppo-health/data/*'

# 5. 构建并启动
cd /opt/oppo-health && docker compose up -d --build

# 6. 网关路由（先备份）
cp -a /opt/service-portal/nginx.conf /opt/service-portal/nginx.conf.backup-$(date +%Y%m%dT%H%M%S)
# 用 deploy/nginx.conf 覆盖后：
docker exec service-portal-gateway nginx -t && docker exec service-portal-gateway nginx -s reload
```

**构建网络注意**：服务器直连 `deb.debian.org` 会超时。`Dockerfile` 已把 Debian 源换成 `mirrors.cloud.tencent.com`、PyPI 换成 `pypi.tuna.tsinghua.edu.cn`。不要改回官方源。

---

## 6. 日常更新

```bash
# 本地改完代码后
scp cloud_mcp.py app_server.py dashboard.html root@115.159.96.81:/opt/oppo-health/
ssh root@115.159.96.81 'cd /opt/oppo-health && docker compose up -d --build'
```

注意：`dashboard.html`、`*.py` 都是**打进镜像**的，只 `docker compose up -d` 不会生效，必须带 `--build`。

---

## 7. 运维命令

```bash
cd /opt/oppo-health

docker compose ps                       # 状态
docker compose logs --tail=100          # 日志
docker compose up -d --build            # 重建
docker restart oppo-health-mcp          # 重启
docker inspect -f '{{.State.Health.Status}}' oppo-health-mcp
```

健康检查（从服务器本机）：

```bash
curl -fsS http://127.0.0.1:9333/healthz
curl -fsS -H "Host: 115.159.96.81" http://127.0.0.1/oppo-health/healthz
```

回滚网关：

```bash
cp -a /opt/service-portal/nginx.conf.backup-20261028T104348 /opt/service-portal/nginx.conf
docker exec service-portal-gateway nginx -t && docker exec service-portal-gateway nginx -s reload
```

---

## 8. 认证续期（Token 到期后）

当前 `auth.json` 的 `accessTokenExp` 是 **2026-11-27 02:13**（+08:00）。到期后云端会返回 `10101`，同步停止。

续期流程（手机需 root + adb 无线调试）：

```bash
# 1. 手机与电脑同一局域网，开发者选项 → 无线调试
adb pair <IP:配对端口> <6位配对码>
adb mdns services                     # 找 _adb-tls-connect 端口
adb connect <IP:连接端口>
adb -s <IP:连接端口> shell "su -c id"  # 必须是 uid=0

# 2. 生成新的 auth.json
python gen_auth.py --serial <IP:连接端口> --out auth.json

# 3. 上传并重启
scp auth.json root@115.159.96.81:/opt/oppo-health/data/
ssh root@115.159.96.81 'chown 10002:10002 /opt/oppo-health/data/auth.json && chmod 600 /opt/oppo-health/data/auth.json && cd /opt/oppo-health && docker restart oppo-health-mcp'
```

`gen_auth.py` 同时兼容两种账号文件格式：

- 旧版：每行一个键（`accessToken=...`）
- OPPO 健康 6.7.17：单行 Java 转义 JSON（`<uid>={"accessToken"\:\"...\"}`）

`ssoid` 从 `shared_prefs/com.sensorsdata.analytics.android.sdk.SensorsDataAPI.xml` 的 `super_properties` 读取。`files/mmkv/*user_ssoid` 和 `databases/cipher_open_usercenter.db` 是加密的，不要在这上面浪费时间。

---

## 9. 数据与隐私

- 健康数据只存在服务器 `/opt/oppo-health/data/cloud_cache.db`，权限 600，属主 10002。
- `auth.json` 是 OPPO 账号凭据，等同密码；不得进 Git、不得贴聊天。
- 面板不把数值写进 HTML 源码、URL 或 localStorage；Token 只存 `sessionStorage`，关标签页即失效。
- 所有 `/api/*` 与 `/mcp` 都要求 `Authorization: Bearer <token>`；未授权返回 401 且不返回任何健康字段。
- `/healthz` 匿名，只返回 `{"status":"ok"}`。
- 统计口径分三类，面板逐项标注：
  - `raw`：云端原始记录
  - `vendor_summary`：OPPO 算法/汇总（原样转述）
  - `local_derived`：本服务计算的描述性统计
- 不做医疗诊断、不给治疗建议、不合成"健康评分"。

---

## 10. 已知限制

1. **目前是 HTTP，不是 HTTPS**（用户 2026-10-28 明确选择暂时保持 HTTP）。Bearer Token 与健康数据在公网明文传输：不要在公共 Wi-Fi、公司网络或任何不可信链路上使用；也不要把面板地址发给别人。需要域名 + 证书才能上 TLS（见第 11 节）。
2. **单实例**。SQLite + 单容器，不要开多个 worker 或多副本。
3. **步骤统计有历史缺口**。`steps_stat` 近 7 天可能返回 0 条（云端确实无数据），120 天窗口实测可拉到 137 条记录。
4. **`sport_file` 不可用**：缺 `encryptKeyEnc`，运动轨迹无法展示。
5. **统计是首版**。`docs/panel-stats-design.md` 里的 67 项完整指标、缺口热力图、一致性校验尚未全部实现。
6. **`refreshToken` 未使用**：不会自动续期，到期必须手动跑 `gen_auth.py`。

---

## 11. 上 HTTPS 的步骤（拿到域名后）

1. 域名 A 记录指向 `115.159.96.81`，安全组放行 443。
2. 安装证书（任选其一）：
   ```bash
   apt-get install -y certbot
   certbot certonly --standalone -d health.example.com
   ```
3. 在 `nginx.conf` 的 `server` 块加 443 监听（模板见 `deploy/nginx-https.snippet.conf`）。
4. 把 `OPPO_HEALTH_PUBLIC_HOSTS` 改成 `health.example.com`，重启容器。
5. 验证：
   ```bash
   curl -I https://health.example.com/oppo-health/healthz
   ```

---

## 12. 验收清单（当前状态）

| 检查 | 结果 |
|---|---|
| 容器 healthy | ✅ |
| `/healthz` 公网 200 | ✅ |
| 面板公网 200 | ✅ |
| `/api/status` 无 Token → 401 | ✅ |
| `/api/status` 有 Token → 200 | ✅ |
| MCP 初始化 + 列工具（4 个） | ✅ |
| `cloud_status` 调用成功 | ✅ |
| 真实云端同步（`steps_detail` 23 条 / `steps_stat` 120 天 137 条） | ✅ |
| 面板桌面 1440 / 移动 390 无横向溢出、无控制台报错 | ✅ |
| 重启容器后自动恢复 | ✅ |
| 本地回归测试 | ✅ 24 passed |
| HTTPS | ❌ 待域名 |
