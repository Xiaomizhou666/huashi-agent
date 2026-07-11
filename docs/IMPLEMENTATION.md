本文件说明“化实”的工具、记忆、中间件与结构化输出如何协同，而不是简单罗列文件名。

# 实现说明

## 1. 工具

### `search_web`

职责是处理需要最新资料、网页证据或明确来源的问题。参数为 `query` 和 1–5 的 `max_results`，返回 `SearchResult`，其中包含成功状态、原查询、最多 5 个 `SourceItem` 和错误信息。

实现使用官方 `langchain-tavily` 的 `TavilySearch`，没有使用已弃用的 community wrapper。客户端用单工作线程包裹调用并设置硬超时；Agent 侧再由 `ToolRetryMiddleware` 对超时和连接失败做有限重试。搜索失败会返回或产生清晰错误，最终回答必须承认失败，禁止伪造来源。`ToolCallLimitMiddleware` 将单轮搜索限制为一次。

### `write_learning_file`

参数包括文件名、内容、格式和是否覆盖。返回 `FileWriteResult`。工具只允许写入配置绑定的 `workspace/outputs/`，只支持 Markdown、TXT、JSON。实现拒绝绝对路径、目录分隔符、`..`、隐藏文件、控制字符、Windows 保留名和扩展名不匹配。默认不覆盖；JSON 在落盘前使用 `json.loads` 验证并格式化。

Agent 在用户要求保存学习笔记、实验报告草稿或知识总结时调用。它不能写 `.env`、Python、可执行文件或工作目录外路径。

### `parse_local_document`

参数是相对于 `workspace/inputs/` 的文件路径，返回 `DocumentParseResult`。TXT 和 Markdown 使用 UTF-8 安全读取；PDF、图片、Word、PPT、Excel 交给独立 `MinerUClient`。

`MinerUClient` 按官方 v4 本地文件流程实现：

1. `POST /api/v4/file-urls/batch` 申请一个上传 URL 和 `batch_id`。
2. 对签名 URL 进行不附加 `Content-Type` 的 `PUT` 上传。
3. `GET /api/v4/extract-results/batch/{batch_id}` 有限轮询。
4. 完成后下载 `full_zip_url`，限制下载大小并防止 ZIP Slip。
5. 在 `workspace/parsed/` 定位 `full.md`，只向模型返回摘要和截断片段。

文件读取前会检查允许目录、文件存在性、符号链接、扩展名和大小。轮询次数与间隔来自环境变量。`FakeMinerUClient` 让默认测试不需要 Token 或网络。

### 长期记忆工具

`get_user_preferences` 读取当前 `user_id` 的白名单偏好；`save_user_preference` 保存一个 `PreferenceKey`。运行时从 `ToolRuntime[HuashiContext]` 获取 `user_id` 和 `allow_memory_write`。

Service 只有检测到明确“记住/以后/跨会话”表达时才把 `allow_memory_write` 设为真，工具本身再次检查该权限。可保存字段包括称呼、学习阶段、回答详细程度、报告格式和学习偏好。Pydantic 校验拒绝明显的 API Key、Token 或密码内容。

## 2. 记忆

`InMemorySaver` 是 Checkpointer，保存 Agent 图状态和消息历史，作用域由 `configurable.thread_id` 决定。它解决“同一个会话继续说”的短期记忆问题，不需要 Agent 自己长期维护 `self.messages`。

`InMemoryStore` 是 Store，用命名空间和键保存跨线程数据。本项目使用 `("huashi", "preferences", user_id)` 命名空间，因此同一用户在不同 `thread_id` 中可以读取相同偏好，而不同用户不会共享。

数据流如下：

1. 前端或 CLI 传入 `message`、`user_id`、`thread_id`。
2. `thread_id` 进入 LangGraph config，Checkpointer 恢复该线程消息。
3. `user_id` 通过 `context_schema=HuashiContext` 进入工具运行时。
4. 读取工具从 Store 获取用户偏好；明确授权时保存工具更新 Store。
5. `reset` 通过生成新的 `thread_id` 创建隔离会话。

二者当前都是内存实现，程序重启后丢失。生产系统应替换为数据库支持的 Checkpointer/Store。

## 3. 中间件

### `ModelRetryMiddleware`

包裹每次模型调用，针对临时超时和连接错误最多重试 2 次，采用有限退避。`ChatOpenAI(max_retries=0)` 避免模型 SDK 与中间件发生重复无限重试。最终失败抛给 Service，Service 转换为结构化错误。

### `ToolRetryMiddleware`

包裹 `search_web` 和 `parse_local_document`，针对外部服务的超时、连接和运行错误最多重试 2 次。最终错误会转换成脱敏、可理解的工具消息，提示模型承认工具不可用并禁止编造结果。

### `ToolCallLimitMiddleware`

全局单轮工具调用上限为 6；联网搜索和复杂文档解析各限制为 1 次。目的是阻止循环调用、意外费用和重复上传。

### `ChemSafetyMiddleware`

在模型调用前读取最近的用户消息，调用 `assess_chem_risk()`。规则覆盖爆炸物/起爆剂、有毒气体释放、受控物质、规避监管和伤害意图，并结合“步骤、配方、比例、纯化、浓缩、放大”等操作性词语。

高风险时，中间件向系统提示追加严格安全上下文，并从该模型调用移除所有执行工具；结构化响应工具由 `response_format` 管理，仍可返回 `AssistantResponse`。Service 还在 Agent 之前执行同一风险评估并直接返回结构化拒绝，形成双层防护。中风险请求不拒绝，但提示模型补充 PPE、通风、监督和废物处理。

## 4. 结构化输出

`AssistantResponse` 是唯一对外最终 Schema，包含意图、答案、工具、来源、生成文件、安全级别和错误字段。嵌套使用 `SourceItem` 与 `GeneratedFile`，并用 Literal、枚举、列表长度和模型校验器约束字段一致性。

Agent 明确配置：

```python
response_format=ToolStrategy(
    AssistantResponse,
    handle_errors="结构化输出校验失败，请严格按 AssistantResponse 字段重试。",
)
```

这比依赖自然语言 JSON 猜测更适合 OpenAI 兼容服务。`HuashiService.chat()` 只读取最终状态中的 `structured_response`，再执行 `AssistantResponse.model_validate()`；不会从最后一条自然语言消息反向猜字段。

若 `structured_response` 缺失或 Pydantic 校验失败，Service 返回 `intent="error"` 的稳定对象。CLI 将对象格式化为易读文本，未来前端直接使用 `model_dump(mode="json")`。

## 5. Web 接口与流式输出

`huashi/api.py` 只处理 HTTP 层职责：Pydantic 请求校验、Jinja2 页面、静态资源、上传分块保存和 `StreamingResponse`。聊天、文件写入和文档解析分别委托给 `HuashiService.chat()`、`chat_stream()`、`write_file()` 与 `parse_document()`，因此 CLI 与 Web 不存在两套业务逻辑。

`HuashiAgentRuntime.stream()` 对同一个已编译 Agent 调用 `graph.stream()`，传入与非流式调用相同的 `thread_id`、`HuashiContext`、Checkpointer、Store 和中间件。流模式同时订阅 `messages` 与 `updates`：

- `messages` 用于提取模型文本、普通工具调用开始和工具结果结束。
- `updates` 与流结束后的线程状态用于读取 `structured_response`。
- 结构化输出工具 `AssistantResponse` 不作为普通工具状态显示。
- 最终 `result` 始终来自 `AssistantResponse.model_validate()`，而不是猜测自然语言字段。

Web 协议使用一行一个事件的 NDJSON。每次正常或异常流均以 `start` 开始、以 `done` 结束；非调试模式只返回友好错误，不返回异常堆栈。详细事件格式与浏览器处理流程见 `docs/FRONTEND.md`。

## 聊天附件与文件上下文

聊天附件由 `huashi/attachments.py` 管理。路由只读取受限上传字节并调用 Service；文件名清理、扩展名/MIME/魔数、大小、哈希去重、安全落盘、线程归属、解析状态与删除均由 `AttachmentManager` 负责。公开 `AttachmentResult` 不含服务器路径或完整正文。

`huashi/document_context.py` 根据 `MAX_ATTACHMENT_CONTEXT_CHARS` 和 `MAX_ATTACHMENT_FILE_CHARS` 构建上下文。Service 在调用原 Agent 前解析附件归属，加入文件名、摘要、受控片段和“不允许虚构文件内容”等规则。后续问题不传附件 ID 时，会读取同一 `user_id + thread_id` 的已解析附件；不同线程显式附件 ID 会被拒绝。

附件上传解析使用独立 NDJSON 阶段事件，聊天回答继续走原 `HuashiAgentRuntime.stream()`。这样文件处理没有绕过 Agent、Checkpointer、中间件或结构化输出。


## 6. Web Heartbeat

Heartbeat 用于在用户停留 Web 对话页面期间提供轻量、友好的会话时长提示，例如烧杯气泡文案和“已经聊了 X 分钟”。它不是聊天回答，也不承担服务保活或后台任务调度职责。

配置位于 `HuashiSettings`：

- `HEARTBEAT_ENABLED`：布尔开关，默认 `true`。
- `HEARTBEAT_INTERVAL_SECONDS`：前端请求周期，默认 120 秒，允许 1～3600 秒。

后端 `huashi/heartbeat.py` 的 `HeartbeatService` 是无状态组件。`GET /api/heartbeat` 接收页面记录的 `session_started_at_ms` 和轮换序号 `sequence`，使用服务器当前时间计算 `elapsed_seconds` 与完整分钟数，再返回一条轮换文案。关闭配置时返回 HTTP 204；内部异常时返回脱敏 503，不传播堆栈。该路径不引用 `HuashiService.chat()`，也不调用模型、Tavily、MinerU、工具或文件解析。

前端 `frontend/static/js/heartbeat.js` 使用一个 `HeartbeatController`：页面初始化后启动递归 `setTimeout`，每次只请求一次轻量 JSON；重复 `start()` 不会创建第二个任务。新建会话调用 `reset()`，重置起点和轮换序号；`visibilitychange` 在页面隐藏时暂停并取消在途请求，重新可见时恢复；浏览器 `offline/online` 事件负责断网暂停与恢复；`pagehide` 会彻底停止并清空会话起点。请求失败只调用弱化的调试回调并安排下一次心跳，不修改聊天按钮或流式状态。

心跳不进入 Agent 记忆，是因为它不是用户意图或模型回答。将其写入 Checkpointer 会污染上下文、增加 Token 消耗并可能干扰结构化输出；写入长期 Store 也不符合“仅保存用户明确偏好”的规则。因此心跳只作为 DOM 中的 `.system-message.heartbeat-message` 展示，最多保留最近 5 条。

当前限制：浏览器后台标签页可能对定时器节流，因此间隔是“至少约为配置值”而非实时调度保证；刷新页面会重新计算本次页面会话起点；多标签页各自维护独立前端计时器。
