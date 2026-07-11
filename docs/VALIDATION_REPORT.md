本文件记录 2026-07-11 在沙盒中对“化实”聊天附件与文件对话版本实际执行的编译、测试、Uvicorn、HTTP 联调和打包检查。

# 验证报告

## 验证环境

- 日期：2026-07-11
- Python：3.13.5
- Web：Uvicorn 0.48.0 / FastAPI 0.128.2
- Node.js：用于原生 JavaScript 语法检查
- 项目目录：`/mnt/data/huashi-agent-work3/huashi-agent`

代码使用 Python 3.11 兼容语法。

## 实际安装的直接依赖

```text
langchain==1.2.18
langchain-openai==1.3.5
langgraph==1.1.10
langchain-tavily==0.2.18
pydantic==2.13.4
pydantic-settings==2.14.2
python-dotenv==1.2.2
httpx==0.28.1
fastapi==0.128.2
uvicorn==0.48.0
Jinja2==3.1.6
python-multipart==0.0.29
pytest==9.1.1
```

本轮在沙盒中执行 `pip install -r requirements.txt`，以上版本安装成功。

## 实际执行命令

```bash
pip install -r requirements.txt
python -m compileall -q .
pytest -q
node --check frontend/static/js/app.js
node --check frontend/static/js/attachments.js
node --check frontend/static/js/stream.js
```

Uvicorn HTTP 联调使用：

```bash
uvicorn huashi_verify_app:app --host 127.0.0.1 --port 8123
```

`huashi_verify_app` 是仅放在 `/tmp` 的离线 Fake 启动模块，调用交付项目中的真实 `create_app()`、`HuashiService`、LangGraph Agent、附件管理和前端资源，不进入压缩包。

同时使用要求中的正式入口完成验证：

```bash
uvicorn app:app --host 127.0.0.1 --port 8000
```

## 编译和 JavaScript

- `python -m compileall -q .`：通过。
- `node --check frontend/static/js/app.js`：通过。
- `node --check frontend/static/js/attachments.js`：通过。
- `node --check frontend/static/js/stream.js`：通过。

## 测试结果

```text
56 passed in 2.17s
```

全部默认测试使用 Fake/Mock，不调用真实外部网络。覆盖：

- 原有普通聊天、流式聊天、联网搜索 Fake、记忆、结构化输出和 CLI 相关逻辑。
- 支持格式上传与 TXT/Markdown 本地解析。
- PDF 使用 `FakeMinerUClient` 成功解析。
- MinerU 超时/失败状态和友好错误。
- 不支持扩展名、MIME 不匹配、文件签名错误、空文件、大文件、过多文件和路径穿越拒绝。
- 同一文件内容在同线程去重。
- 附件绑定 `user_id/thread_id`，跨线程和跨用户拒绝。
- 上传后文件问答和同一线程连续追问。
- 多文件回答。
- 文件不存在答案时输出“上传文件中未找到”，不伪造文件内容。
- 仅上传文件、空问题时使用默认处理提示。
- 附件上传与解析事件顺序。
- 聊天 `token -> result -> done` 顺序。
- 删除附件与重置后不继承。
- 带附件的危险化学请求仍进入安全拒绝。
- 页面不存在旧 `uploadForm/documentInput`，存在聊天 `attachmentInput` 和待发送附件区域。
- 前端无 CDN，源码无明显硬编码密钥。

## Uvicorn HTTP 文件对话联调

本轮实际启动离线 Fake Uvicorn 应用并完成以下 HTTP 流程：

1. `GET /api/health`：200。
2. `GET /`：200。
3. `GET /static/images/logo.svg`：200。
4. 检查首页不含旧 `uploadForm`，包含 `attachmentInput`。
5. `POST /api/reset` 创建线程。
6. 通过 `POST /api/chat/attachments` 上传 `实验报告.md`。
7. 收到事件：

```text
start
upload_start
upload_end
parse_start
parse_progress
tool_start
tool_end
parse_end
result
done
```

8. `POST /api/chat/stream` 提问“文件中的实验现象是什么”。
9. 收到 `start -> token -> result -> done`。
10. 回答包含文件名“实验报告.md”和文件内容“浅粉色”。
11. 不携带附件 ID 再次调用普通 `/api/chat`，回答仍依据同一文件，验证连续追问。

该流程复用了交付项目的 Service、Agent、Checkpointer、附件上下文和结构化响应，没有使用静态假页面。

## 正式入口验证

实际执行：

```bash
uvicorn app:app --host 127.0.0.1 --port 8000
```

在未配置真实密钥的环境中，正式入口成功启动；`GET /api/health`、`GET /` 和 `GET /static/images/logo.svg` 均返回 200。健康检查只返回能力布尔值，首页不含旧独立上传表单，并包含聊天附件输入区。

## 页面交互验证

通过模板检查、JavaScript 语法和接口联调确认：

- 左侧独立文件上传区已删除。
- 输入区存在附件按钮、多选 input、待发送卡片和移除按钮逻辑。
- 解析阶段可更新等待上传、上传中、解析中、已解析和失败状态。
- 生成期间禁用发送、新会话和附件操作。
- 当前线程资料条支持删除附件。
- 流异常会恢复按钮，并以友好错误显示。

本轮没有安装浏览器自动化驱动，因此没有执行真实鼠标点击或像素级截图回归；不声称跨浏览器视觉效果已逐一验证。

## CLI 回归

正式打包前执行：

```bash
printf 'help\nquit\n' | python main.py
```

实际检查结果：CLI 正常启动、显示当前 `thread_id`、输出帮助并通过 `quit` 退出，未被 Web 附件代码替换。

## 真实外部 API

项目提供者说明原版本已完成真实模型、Tavily 和 MinerU API 测试。

本次没有重新调用真实模型、Tavily 或 MinerU；本轮主要验证前端附件、解析流、文件问答、连续追问和线程隔离。因此本报告只对本轮实际执行的 Fake/Mock 与本地 HTTP 联调负责。

## 安全与打包检查

- 未使用 `deepagents`。
- 未修改真实模型、Tavily 或 MinerU 配置方式。
- 未读取、输出或打包 `.env`。
- API 不返回附件绝对路径或完整解析文本。
- 非调试模式不返回异常堆栈。
- 上传目录、数量、大小、文件名、扩展名、MIME 和签名均受限制。
- 文件上下文有总字符和单文件字符预算。
- 压缩包排除 `.env`、虚拟环境、缓存、用户上传、解析产物、日志和临时文件。

## 已知限制

- 附件和记忆为进程内状态，服务重启后可能丢失。
- 多 Uvicorn worker 不共享附件上下文。
- 没有向量检索，文件问答只使用摘要和受控片段。
- 未执行跨浏览器自动化和视觉回归。
- 本轮未重新验证真实外部 API。

## 压缩包解压复测

生成候选 `huashi-agent.zip` 后，直接检查 ZIP 清单并解压到全新目录：

- ZIP 完整性检查：通过。
- 归档文件数：52。
- 未包含 `.env`、`__pycache__`、`.pytest_cache`、虚拟环境、日志、临时文件、嵌套 ZIP 或用户工作文件。
- 工作目录中只保留三个 `.gitkeep`。

解压后实际执行：

```text
python -m compileall -q .       通过
pytest -q                       56 passed in 2.27s
node --check app.js             通过
node --check attachments.js     通过
node --check stream.js          通过
CLI help/quit                    通过
```

复测生成的缓存随后被删除，不进入交付包。


# Heartbeat 增强验证（2026-07-11）

本节记录本次 Heartbeat 增量开发的实际执行结果，并覆盖前文旧版本的测试总数。没有重新调用真实模型、Tavily 或 MinerU。

## 运行环境

- Python：3.13.5。
- Node.js：22.16.0。
- Web：FastAPI 0.128.2、Uvicorn 0.48.0。
- 测试依赖从项目现有 `requirements.txt` 安装到临时虚拟环境；虚拟环境不进入交付包。

## 实际执行命令

```bash
python -m compileall .
pytest -q
node --check frontend/static/js/app.js
node --check frontend/static/js/attachments.js
node --check frontend/static/js/stream.js
node --check frontend/static/js/heartbeat.js
printf 'help\nquit\n' | python main.py
HEARTBEAT_ENABLED=true HEARTBEAT_INTERVAL_SECONDS=1 \
  uvicorn app:app --host 127.0.0.1 --port 8137
```

## 测试结果

```text
63 passed in 2.67s
```

新增 Heartbeat 测试覆盖：

- 从环境变量读取 `HEARTBEAT_ENABLED` 与 `HEARTBEAT_INTERVAL_SECONDS`。
- 关闭时 Service 不生成文案且 HTTP 返回 204。
- 开启时轮换三类文案，并正确计算 300 秒为 5 分钟。
- Heartbeat 请求不访问 Agent Runtime、模型、工具、Checkpointer 或长期 Store。
- Heartbeat 内部异常返回脱敏 503，随后普通聊天仍成功。
- 前端控制器重复 `start()` 只保留一个定时器。
- `reset()` 重置会话起点和轮换序号。
- `pause/resume/stop` 正确清理和恢复任务。
- 关闭配置时前端不创建定时器。
- 页面模板、前端模块、可见性、断网和离页清理事件已接线。

## 实际 Web 验证

正式 `app:app` 在 127.0.0.1:8137 启动成功。实际检查：

- `GET /` 返回 200。
- 页面 HTML 渲染 `data-heartbeat-enabled="true"` 与 `data-heartbeat-interval-seconds="1"`。
- 使用“当前时间减 125 秒”的会话起点访问 `/api/heartbeat`，返回 `elapsed_seconds=125`、`elapsed_minutes=2`，文案包含“已经聊了 2 分钟”。
- 前端 `HeartbeatController` 以 1 秒间隔连接真实 Uvicorn 端点，收到“咕噜咕噜……化石又沉了下去。”，随后 `stop()` 后状态为无定时器、无在途请求。
- CLI `help/quit` 正常，说明 Web Heartbeat 未影响 CLI。

尝试使用沙盒自带 Headless Chromium 打开本地页面并检查 `.heartbeat-message`，但浏览器组织策略阻止 `127.0.0.1`、自定义本地域名和 `file://` URL。因而未声称完成真实浏览器视觉自动化；页面展示逻辑由真实 HTTP 联调、Node 控制器执行和静态 DOM 接线测试共同验证。

## 外部 API

本次未重新调用真实模型、Tavily 或 MinerU。Heartbeat 路径按设计不调用任何收费 API；原有真实 API 配置和调用代码未改动。

## 当前限制

- 浏览器后台标签页可能节流定时器，因此提示不保证实时精确到秒。
- 页面刷新会重置本次页面会话的 Heartbeat 起点。
- 多标签页各自维护独立计时器。
- 沙盒浏览器策略阻止本地页面视觉自动化。

## Heartbeat 版本压缩包复测

生成候选压缩包后解压到全新目录并再次实际执行：

```text
ZIP 完整性                     通过
归档文件数                    55
python -m compileall -q .      通过
pytest -q                      63 passed in 3.28s
四个 JavaScript 模块语法检查  通过
CLI help/quit                  通过
```

归档清单未包含 `.env`、真实凭据、虚拟环境、Python/pytest 缓存、日志、临时文件、用户上传文件或解析产物；三个工作目录仅包含 `.gitkeep`。
