# 连接远程 MCP（各 Agent 配置）

服务端：

```
URL:    http://115.159.96.81/oppo-health/mcp
协议:   MCP Streamable HTTP
鉴权:   Authorization: Bearer <MCP_TOKEN>
```

`<MCP_TOKEN>` 在服务器 `/opt/oppo-health/private/service.env` 的 `OPPO_HEALTH_API_TOKEN`，不要写进公开仓库。

> 不同客户端字段名不一样，但核心只有三件事：URL、`streamable-http` 传输、Bearer 头。

---

## 1. Claude Desktop / Claude Code

`claude_desktop_config.json`（Windows：`%APPDATA%\Claude\claude_desktop_config.json`）：

```json
{
  "mcpServers": {
    "oppo-health": {
      "type": "http",
      "url": "http://115.159.96.81/oppo-health/mcp",
      "headers": {
        "Authorization": "Bearer <MCP_TOKEN>"
      }
    }
  }
}
```

Claude Code CLI：

```bash
claude mcp add --transport http oppo-health \
  http://115.159.96.81/oppo-health/mcp \
  --header "Authorization: Bearer <MCP_TOKEN>"
```

---

## 2. Cursor

`~/.cursor/mcp.json`：

```json
{
  "mcpServers": {
    "oppo-health": {
      "url": "http://115.159.96.81/oppo-health/mcp",
      "headers": {
        "Authorization": "Bearer <MCP_TOKEN>"
      }
    }
  }
}
```

---

## 3. VS Code（Cline / Continue / Copilot MCP）

`.vscode/mcp.json`：

```json
{
  "servers": {
    "oppo-health": {
      "type": "http",
      "url": "http://115.159.96.81/oppo-health/mcp",
      "headers": {
        "Authorization": "Bearer <MCP_TOKEN>"
      }
    }
  }
}
```

---

## 4. Hermes Agent

```bash
hermes mcp add oppo-health \
  --url http://115.159.96.81/oppo-health/mcp \
  --header "Authorization: Bearer <MCP_TOKEN>"
```

加完需要新会话或 `/reload-mcp` 才生效。

---

## 5. Python（官方 MCP SDK）

```python
import asyncio
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

URL = "http://115.159.96.81/oppo-health/mcp"
TOKEN = "<MCP_TOKEN>"


async def main():
    headers = {"Authorization": f"Bearer {TOKEN}"}
    async with httpx.AsyncClient(headers=headers, timeout=60) as client:
        async with streamable_http_client(URL, http_client=client) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                print([tool.name for tool in tools.tools])
                result = await session.call_tool("cloud_status", {})
                print(result.content[0].text)


asyncio.run(main())
```

依赖：`pip install "mcp[cli]<2" httpx`

---

## 6. Node / TypeScript

```ts
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

const transport = new StreamableHTTPClientTransport(
  new URL("http://115.159.96.81/oppo-health/mcp"),
  { requestInit: { headers: { Authorization: "Bearer <MCP_TOKEN>" } } }
);

const client = new Client({ name: "oppo-health-client", version: "1.0.0" });
await client.connect(transport);
console.log(await client.listTools());
```

---

## 7. 纯 HTTP 调试（curl）

```bash
TOKEN=<MCP_TOKEN>

# 存活
curl -s http://115.159.96.81/oppo-health/healthz

# 未授权必须 401
curl -s -o /dev/null -w '%{http_code}\n' http://115.159.96.81/oppo-health/api/status

# 授权状态
curl -s -H "Authorization: Bearer $TOKEN" \
  http://115.159.96.81/oppo-health/api/status

# 摘要
curl -s -H "Authorization: Bearer $TOKEN" \
  http://115.159.96.81/oppo-health/api/summary

# 触发同步（days ≤ 7 更稳）
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"metric":"heart_rate_detail","days":7}' \
  http://115.159.96.81/oppo-health/api/sync
```

MCP 的 `initialize` 需要 `Accept: application/json, text/event-stream`，直接用 curl 手搓比较麻烦，建议用上面的 SDK 示例。

---

## 8. 工具调用建议

- 先 `cloud_status()` 看缓存里有什么，再决定拉什么。
- `cloud_sync` 单次窗口建议 ≤ 7 天；`steps_detail` 120 天约 25 秒，`app_usage` 60 天约 63 秒，别在交互式对话里拉全量。
- `cloud_analyze(days)` 返回的每个数字都带 `provenance`：
  - `raw` 原始记录
  - `vendor_summary` OPPO 算法结果
  - `local_derived` 本服务计算
  引用数据时保留这个标签，不要把厂商评分说成"你的健康评分"。
- 数据稀疏（血氧、HRV、压力）时先看 `samples`，单点不要当趋势。
