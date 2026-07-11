本文件说明“化实”聊天附件前端的交互调整、技术边界、流式协议、线程绑定和页面设计。

# Web 聊天附件与文件对话

## 为什么移除独立上传区

原页面把“上传解析”放在左侧独立表单中，解析结果没有自然进入聊天上下文，用户需要在“解析文件”和“提出问题”之间手动切换。本次删除了 `uploadForm`、`documentInput`、独立解析状态面板及相关事件监听，将文件处理变为聊天发送动作的一部分。

底层 `DocumentParser`、MinerU Client 和兼容 `/api/read-file` 接口仍保留，没有推翻原后端架构。

## 技术选型

- FastAPI：请求校验、上传接口、普通与流式响应。
- Jinja2：渲染单页模板及上传限制参数。
- 原生 HTML/CSS/JavaScript：无 Vue、React、jQuery 或外部 CDN。
- `app.js`：会话、聊天、结果面板与笔记弹窗。
- `attachments.js`：待上传文件、线程附件、状态卡片和删除操作。
- `stream.js`：通用 NDJSON 流读取。

## 页面结构

### 左侧

- Logo 与“化实”名称。
- 新建会话。
- `user_id` 与 `thread_id`。
- 能力说明。
- 保存学习笔记按钮。
- 精简实验安全提示。

左侧不再包含文件选择、MinerU 提交按钮或独立解析结果。

### 中央聊天区

- 欢迎信息和示例问题。
- 用户与智能体消息。
- 用户消息内的附件卡片。
- 上传、解析和工具运行状态。
- 回答依据文件、联网来源、安全提示和生成文件。
- 当前线程附件条，可删除不再使用的文件。

### 底部输入区

- 附件按钮。
- 多行问题输入框。
- 发送按钮。
- 待发送附件预览与移除。
- 文件类型、大小和数量提示。

## 前后端调用流程

```text
浏览器选择 1～3 个文件
  -> 前端校验扩展名、名称、大小和重复选择
  -> POST /api/chat/attachments (multipart/form-data)
  -> API 读取受限字节，不接受用户原始路径
  -> HuashiService.create_attachment()
  -> AttachmentManager 安全落盘、哈希去重、绑定 user_id/thread_id
  -> HuashiService.parse_attachment()
  -> 原 DocumentParser：txt/md 本地读取，其他格式 MinerU
  -> upload_* / parse_* / tool_* NDJSON 事件
  -> 前端取得 attachment_id
  -> POST /api/chat/stream
  -> HuashiService 根据附件归属构建受控文件上下文
  -> 原 LangGraph Agent、记忆、中间件和结构化输出
  -> token / result / done
```

上传和回答采用两段接口，但前端把它们串成一次发送操作。这样附件解析错误可以在调用模型前明确处理，同时聊天流仍保持原 JSON 请求模型和稳定客户端接口。

## 附件状态

公开状态包括：

```text
waiting_upload  等待上传
uploading       上传中
waiting_parse   等待解析
parsing         解析中
parsed          已解析
failed          解析失败
```

解析失败时，输入框内容和待发送附件仍保留，用户可以修正后重试。生成过程中发送、附件和新会话按钮会被禁用，流结束后恢复。

## 流事件

附件接口：

```json
{"event":"start","data":{"user_id":"student","thread_id":"..."}}
{"event":"upload_start","data":{"filename":"实验报告.pdf"}}
{"event":"upload_end","data":{"attachment_id":"att_xxx","parse_status":"waiting_parse"}}
{"event":"parse_start","data":{"attachment_id":"att_xxx"}}
{"event":"parse_progress","data":{"message":"正在提取可用于问答的文本"}}
{"event":"tool_start","data":{"name":"parse_local_document"}}
{"event":"tool_end","data":{"name":"parse_local_document","success":true}}
{"event":"parse_end","data":{"attachment_id":"att_xxx","success":true}}
{"event":"result","data":{"success":true,"attachments":[]}}
{"event":"done","data":{}}
```

回答接口：

```json
{"event":"start","data":{}}
{"event":"tool_start","data":{"name":"search_web"}}
{"event":"token","data":{"text":"根据你上传的实验报告……"}}
{"event":"result","data":{"answer":"...","attachments":[],"sources":[]}}
{"event":"done","data":{}}
```

异常流也保证以 `done` 结束。非调试模式不返回堆栈、Token、绝对路径或完整文档内容。

## 会话附件绑定

`AttachmentManager` 在进程内保存：

- `attachment_id`
- 原始安全文件名
- 内部安全路径
- 文件类型、MIME 和大小
- SHA-256
- 解析状态、摘要与受控文本
- `user_id`、`thread_id`
- 创建时间

公开 API 不返回内部路径或完整解析正文。

显式附件 ID 必须同时匹配当前 `user_id` 和 `thread_id`。不传 ID 时，Service 会读取当前线程已解析附件，支持连续追问。新建会话会生成新的 `thread_id`，并清理前端显式重置的旧线程临时附件。

## 上下文控制

`DocumentContextBuilder` 使用两层字符预算：

- `MAX_ATTACHMENT_CONTEXT_CHARS`：所有文件合计最大字符数。
- `MAX_ATTACHMENT_FILE_CHARS`：单文件最大字符数。

上下文包含文件名、摘要和解析片段，并加入“文件没有答案时必须明确说明”等规则。当前未引入向量数据库；这是有意保持的学习版简化。

## 文件安全

聊天上传只支持白名单扩展名和 MIME，检查常见文件魔数。拒绝：

- `.env`
- Python/Shell 脚本
- 可执行文件
- ZIP 等压缩包
- 空文件
- 超限文件
- 路径穿越、绝对路径和隐藏文件
- MIME 与扩展名明显不匹配的文件

实际路径由服务生成，使用哈希命名空间和随机 `attachment_id`，不使用客户端路径。

## 视觉设计

保留“恐龙头骨泡在烧杯液体中”的 Logo。页面继续使用玻璃蓝、青绿色、浅奶油色和气泡/实验室氛围，但减少侧栏卡片和空白，将注意力集中在聊天与附件状态。

桌面端使用窄侧栏和大聊天面板；移动端把侧栏压缩为顶部区域，附件、消息和发送保持基本可用。

## 已知限制

- 附件记录为进程内状态，服务重启后可能失效。
- 多 worker 之间不共享附件。
- 没有向量检索，超出截断片段的信息可能不可见。
- Fetch 无原生上传百分比，本页面展示阶段状态而非精确字节进度。
- 本轮未执行跨浏览器自动化和像素级回归。


## Web Heartbeat

页面从模板读取 `data-heartbeat-enabled` 与 `data-heartbeat-interval-seconds`，由独立 `HeartbeatController` 管理。它通过 `GET /api/heartbeat` 获取不含聊天内容的轻量文案，并渲染为低视觉权重的系统消息。该请求与 `/api/chat/stream`、附件上传流互相独立，因此心跳错误不会终止回答。

控制器使用单个递归 `setTimeout`，重复启动会被拒绝；页面隐藏或断网时暂停并中止在途请求，重新可见或恢复联网后继续；新建会话时清零，离页时清理。心跳不会被添加到用户消息、Agent 输入、附件上下文或结构化结果。
