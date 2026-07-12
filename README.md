本文件介绍“化实”学习版项目的后端、聊天附件、流式文件问答、安装运行方式、测试结果与安全边界。

# 化实：化学实验学习智能助手

<p align="center">
  <img src="frontend/static/images/logo.svg" width="168" alt="化实 Logo：恐龙头骨泡在烧杯液体中">
</p>

“化实”是一个基于 LangChain 1.2、LangGraph、Pydantic、Tavily、MinerU 与 FastAPI 的单智能体学习项目。项目同时提供 CLI 与轻量中文 Web 前端，支持把实验报告、课件、图片或笔记直接放进聊天窗口，解析后围绕同一文件连续提问。

<img  alt="image" src="https://github.com/user-attachments/assets/1745a1d9-046c-4b8b-b5c7-17f4873c6f17" />

## 主要能力

- 中文化学知识、实验现象、仪器用途、失败排查和实验报告问答。
- 使用 `langchain-tavily` 联网检索并保留标题、URL 与摘要来源。
- Markdown、TXT、JSON 安全写入 `workspace/outputs/`，默认不覆盖。
- TXT/Markdown 本地解析；PDF、Office 与图片通过现有 MinerU Client 解析。
- 聊天输入区支持 1～3 个附件，实时展示上传、解析与错误状态。
- 附件按 `user_id + thread_id` 绑定，同一会话可持续追问，不同会话严格隔离。
- 文件上下文按字符预算截断，不把大型文档全文无控制地交给模型。
- `InMemorySaver` 提供线程短期记忆，`InMemoryStore` 保存明确授权的学习偏好。
- `ToolStrategy(AssistantResponse)` 返回稳定的 Pydantic 结构化结果。
- 实际接入模型重试、工具重试、工具调用限制和化学安全中间件。
- CLI、普通 API、流式 API 和前端共用同一个 `HuashiService`。

## Web 页面

页面以聊天为中心。左侧仅保留 Logo、新建会话、身份信息、能力说明、安全提示和轻量“保存学习笔记”入口；原独立文件解析表单已经移除。

底部输入区提供附件按钮。文件发送前显示名称、类型、大小和移除按钮；发送后依次显示上传、解析和工具状态。解析成功的文件会出现在“本会话资料”条中，后续问题会继续参考这些文件，用户也可随时删除附件。

## 支持文件类型与限制

聊天附件至少支持：

```text
PDF
DOC / DOCX
PPT / PPTX
XLS / XLSX
TXT
Markdown
PNG
JPG / JPEG
```

默认限制：

- 单次最多 3 个文件，可通过 `MAX_ATTACHMENTS_PER_MESSAGE` 调整。
- 单文件默认不超过 20 MB，可通过 `MAX_FILE_SIZE_MB` 调整。
- 文件名默认最长 180 字符。
- 拒绝隐藏文件、路径穿越、绝对路径、脚本、可执行文件和压缩包。
- 校验扩展名、MIME 类型与常见文件签名。
- 上传文件只保存到 `workspace/inputs/attachments/` 下的隔离目录。

## 文件对话流程

```text
选择附件
  -> 输入问题并发送
  -> POST /api/chat/attachments
  -> 安全保存并生成 attachment_id
  -> TXT/Markdown 本地解析，其他格式调用 MinerU
  -> 页面显示上传/解析事件
  -> POST /api/chat/stream，携带 attachment_id
  -> Service 构建受长度控制的文件上下文
  -> 原 LangGraph Agent 流式回答
  -> 同一 thread_id 后续问题自动继续参考已解析附件
```

文件回答规则：

- 优先依据上传文件，并在回答中注明文件名。
- 文件中没有答案时明确说明，不得虚构。
- 只有解析结果确实包含章节、标题或页码时才引用位置。
- 联网补充与文件内容分开说明。
- 解析失败的文件不会被当作已读取资料。

## 项目结构

```text
huashi-agent/
├── app.py
├── main.py
├── README.md
├── requirements.txt
├── .env.example
├── .gitignore
├── frontend/
│   ├── templates/index.html
│   └── static/
│       ├── css/style.css
│       ├── js/
│       │   ├── app.js
│       │   ├── attachments.js
│       │   └── stream.js
│       └── images/
│           ├── logo.svg
│           └── favicon.svg
├── workspace/
│   ├── inputs/
│   ├── outputs/
│   └── parsed/
├── huashi/
│   ├── api.py
│   ├── agent.py
│   ├── service.py
│   ├── attachments.py
│   ├── document_context.py
│   ├── config.py
│   ├── models.py
│   ├── prompts.py
│   ├── memory.py
│   ├── middleware.py
│   ├── testing.py
│   ├── tools/
│   └── clients/
├── docs/
│   ├── IMPLEMENTATION.md
│   ├── FRONTEND.md
│   ├── SAFETY.md
│   └── VALIDATION_REPORT.md
└── tests/
```

## 环境与安装

推荐 Python 3.11～3.13。

```bash
python -m venv .venv
source .venv/bin/activate       # Linux/macOS
# .venv\Scripts\Activate.ps1   # Windows PowerShell

pip install -r requirements.txt
cp .env.example .env
```

## `.env` 配置

```dotenv
VOLCENGINE_API_KEY=
VOLCENGINE_BASE_URL=
CHAT_MODEL=doubao-seed-2.0-mini
EMBEDDING_MODEL=doubao-embedding-vision

TAVILY_API_KEY=
MINERU_API_TOKEN=
MINERU_API_BASE_URL=https://mineru.net

LANGSMITH_TRACING=false
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
LANGSMITH_API_KEY=
LANGSMITH_PROJECT=huashi-agent

WORKSPACE_DIR=workspace
MAX_FILE_SIZE_MB=20
MAX_ATTACHMENTS_PER_MESSAGE=3
MAX_ATTACHMENT_CONTEXT_CHARS=12000
MAX_ATTACHMENT_FILE_CHARS=6000
MAX_FILENAME_LENGTH=180
MINERU_POLL_INTERVAL_SECONDS=3
MINERU_MAX_POLL_ATTEMPTS=40
TAVILY_TIMEOUT_SECONDS=15
MODEL_TIMEOUT_SECONDS=60
HUASHI_DEBUG=false

HEARTBEAT_ENABLED=true
HEARTBEAT_INTERVAL_SECONDS=120
```

`EMBEDDING_MODEL` 仍只为未来 RAG 预留，本次没有引入向量数据库。缺少 Tavily 或 MinerU 配置时，基本聊天、文件写入和 TXT/Markdown 文件对话仍可使用。真实 `.env` 不得提交或打包。

## Web 启动

```bash
uvicorn app:app --host 127.0.0.1 --port 8000
```

浏览器访问 `http://127.0.0.1:8000`，API 文档位于 `/api/docs`。

主要接口：

```text
GET    /
GET    /api/health
GET    /api/heartbeat
POST   /api/chat
POST   /api/chat/stream
POST   /api/chat/attachments
GET    /api/chat/attachments
DELETE /api/chat/attachments/{attachment_id}
POST   /api/reset
POST   /api/write-file
POST   /api/read-file              # 保留的兼容解析接口
```

## 流事件

聊天附件接口和回答接口均返回 `application/x-ndjson`。浏览器使用 Fetch `ReadableStream` 逐行读取：

```json
{"event":"upload_start","data":{"filename":"实验报告.pdf"}}
{"event":"upload_end","data":{"attachment_id":"att_xxx"}}
{"event":"parse_start","data":{"attachment_id":"att_xxx"}}
{"event":"parse_progress","data":{"message":"正在提取可用于问答的文本"}}
{"event":"parse_end","data":{"success":true}}
{"event":"tool_start","data":{"name":"parse_local_document"}}
{"event":"tool_end","data":{"name":"parse_local_document","success":true}}
{"event":"token","data":{"text":"根据实验报告……"}}
{"event":"result","data":{"answer":"...","attachments":[],"sources":[]}}
{"event":"done","data":{}}
```

流式回答继续通过原有 LangGraph Agent、Checkpointer、Store、工具、中间件与安全规则，不会绕过后端业务逻辑。

## Web Heartbeat

Web 页面默认按 `HEARTBEAT_INTERVAL_SECONDS` 周期显示一条弱化的“烧杯心跳”系统消息。`HEARTBEAT_ENABLED=false` 可完全关闭。心跳由独立 `/api/heartbeat` 端点生成轮换文案，并根据页面本次会话起点计算已持续分钟数。

心跳不会调用模型、Tavily、MinerU 或任何 Agent 工具，也不会进入 Checkpointer、长期 Store、聊天消息或 `AssistantResponse`。页面隐藏时暂停；重新可见时恢复；新建会话时计时归零；离开页面时清理定时器和未完成请求。心跳失败只会跳过本次提示，不影响普通聊天和文件问答。

## 使用示例

1. 点击输入框左侧附件按钮。
2. 选择 `实验报告.pdf`。
3. 输入“总结实验目的，并指出误差来源”。
4. 等待附件显示“已解析”，随后回答逐段出现。
5. 继续输入“第二个误差如何改进？”；无需再次上传。
6. 点击“本会话资料”中的 `×` 可停止后续问题继续使用该文件。

只上传文件而未输入问题时，系统使用默认提示：

> 请告诉我你希望针对该文件进行总结、提取、问答还是其他处理。

## CLI

```bash
python main.py
```

支持 `help`、`reset`、`read 文件名`、`write 文件名 | 内容` 和 `quit`。CLI 保留原有行为，不依赖 Web 前端。

## Python 接口

```python
from huashi.service import HuashiService

service = HuashiService()
thread_id = service.new_thread_id()
response = service.chat(
    "解释蒸馏与分馏的区别",
    user_id="student-001",
    thread_id=thread_id,
)
print(response.model_dump(mode="json"))
```

聊天附件由 Web 路由读取后交给 `HuashiService.create_attachment()` 与 `parse_attachment()`；前端和其他客户端只持有 `attachment_id`，不会获得服务器路径或完整解析正文。

## 测试

```bash
python -m compileall .
pytest -q
node --check frontend/static/js/app.js
node --check frontend/static/js/attachments.js
node --check frontend/static/js/stream.js
node --check frontend/static/js/heartbeat.js
printf 'help\nquit\n' | python main.py
```

本次验证结果为 **63 passed**。真实模型、Tavily 和 MinerU API 已在原版本中完成验证；本次修改没有重新产生真实外部 API 调用，主要验证了文件上传、解析事件、文件问答、连续追问、线程隔离、前端联调和 CLI 回归。

## 已知限制

- 附件索引、短期记忆和长期记忆均为进程内实现；服务重启后附件上下文可能失效。
- 多 Uvicorn worker 不共享附件或会话状态，学习版建议单 worker 运行。
- 当前使用截断与字符预算控制上下文，没有向量检索或复杂 RAG。
- 文档解析只保存摘要和受控片段供问答，超出片段的信息可能无法回答。
- 浏览器 `localStorage` 仅用于保存学习版 `user_id/thread_id`，不是登录认证。
- Web Heartbeat 使用浏览器单定时器与轻量轮询；页面关闭或 JavaScript 被系统节流时不会保证严格准点。
- 本轮尝试了 Headless Chromium 页面验证，但沙盒组织策略阻止本地 URL；已改用真实 Uvicorn HTTP、Node 生命周期测试和静态 DOM 接线检查。

## 化学安全声明

本项目用于学习和展示，不能替代教师、实验室负责人、机构安全制度、安全数据表（SDS）、医疗建议或应急处置。高风险化学请求会拒绝直接执行细节，并转向理论、虚拟实验、低风险演示或专业监督建议。
