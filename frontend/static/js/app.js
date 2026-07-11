/** 驱动“化实”聊天、附件问答、流式状态和轻量文件写入界面。 */
import { AttachmentController, renderMessageAttachments } from "./attachments.js";
import { parseNdjson } from "./stream.js";
import { HeartbeatController } from "./heartbeat.js";

const dom = {
  newSessionButton: document.querySelector("#newSessionButton"),
  userIdInput: document.querySelector("#userIdInput"),
  threadIdValue: document.querySelector("#threadIdValue"),
  healthBadge: document.querySelector("#healthBadge"),
  messageList: document.querySelector("#messageList"),
  chatForm: document.querySelector("#chatForm"),
  messageInput: document.querySelector("#messageInput"),
  sendButton: document.querySelector("#sendButton"),
  processBar: document.querySelector("#processBar"),
  processText: document.querySelector("#processText"),
  toast: document.querySelector("#toast"),
  attachmentInput: document.querySelector("#attachmentInput"),
  attachmentButton: document.querySelector("#attachmentButton"),
  selectedAttachmentList: document.querySelector("#selectedAttachmentList"),
  threadAttachmentBar: document.querySelector("#threadAttachmentBar"),
  threadAttachmentList: document.querySelector("#threadAttachmentList"),
  attachmentHint: document.querySelector("#attachmentHint"),
  writeFileDialog: document.querySelector("#writeFileDialog"),
  openWriteFileButton: document.querySelector("#openWriteFileButton"),
  closeWriteFileButton: document.querySelector("#closeWriteFileButton"),
  cancelWriteFileButton: document.querySelector("#cancelWriteFileButton"),
  writeFileForm: document.querySelector("#writeFileForm"),
  writeFileStatus: document.querySelector("#writeFileStatus"),
};

const state = {
  userId: localStorage.getItem("huashi-user-id") || "student",
  threadId: localStorage.getItem("huashi-thread-id") || "",
  streaming: false,
};

const maxSizeMb = Number(document.body.dataset.maxFileSizeMb || 20);
const maxAttachments = Number(document.body.dataset.maxAttachments || 3);
const heartbeat = new HeartbeatController({
  enabled: document.body.dataset.heartbeatEnabled === "true",
  intervalSeconds: Number(document.body.dataset.heartbeatIntervalSeconds || 120),
  onMessage: createHeartbeatMessage,
  onError: (error) => console.debug("Heartbeat skipped", error?.message || error),
});
dom.attachmentHint.textContent = `支持 PDF、Office、图片、TXT、Markdown · 单个 ≤ ${maxSizeMb} MB · 最多 ${maxAttachments} 个`;

function showToast(message) {
  dom.toast.textContent = message;
  dom.toast.hidden = false;
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => { dom.toast.hidden = true; }, 3300);
}

function setProcess(message, visible = true) {
  dom.processText.textContent = message;
  dom.processBar.hidden = !visible;
}

function setStatus(element, message, type = "") {
  element.textContent = message;
  element.className = `form-status ${type}`.trim();
}

function persistIdentity() {
  const cleaned = dom.userIdInput.value.trim() || "student";
  state.userId = cleaned.replace(/[^\w.@+-]/g, "-").slice(0, 120) || "student";
  dom.userIdInput.value = state.userId;
  localStorage.setItem("huashi-user-id", state.userId);
  if (state.threadId) localStorage.setItem("huashi-thread-id", state.threadId);
  dom.threadIdValue.textContent = state.threadId || "准备中…";
}

function scrollChat() {
  requestAnimationFrame(() => { dom.messageList.scrollTop = dom.messageList.scrollHeight; });
}

function createHeartbeatMessage(text) {
  const article = document.createElement("article");
  article.className = "message system-message heartbeat-message";
  const body = document.createElement("div");
  body.className = "message-body";
  const label = document.createElement("div");
  label.className = "message-label";
  label.textContent = "烧杯心跳";
  const bubble = document.createElement("div");
  bubble.className = "heartbeat-bubble";
  bubble.textContent = text;
  body.append(label, bubble);
  article.appendChild(body);
  dom.messageList.appendChild(article);
  const oldMessages = dom.messageList.querySelectorAll(".heartbeat-message");
  if (oldMessages.length > 5) oldMessages[0].remove();
  scrollChat();
}

function createMessage(role, text, attachments = []) {
  const article = document.createElement("article");
  article.className = `message ${role}-message`;
  if (role === "assistant") {
    const avatar = document.createElement("img");
    avatar.className = "avatar";
    avatar.src = "/static/images/favicon.svg";
    avatar.alt = "";
    article.appendChild(avatar);
  }
  const body = document.createElement("div");
  body.className = "message-body";
  const label = document.createElement("div");
  label.className = "message-label";
  label.textContent = role === "user" ? "你" : "化实";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  body.append(label, bubble);
  renderMessageAttachments(body, attachments);
  article.appendChild(body);
  dom.messageList.appendChild(article);
  scrollChat();
  return { article, body, bubble };
}

function ensureToolStrip(messageView) {
  let strip = messageView.body.querySelector(".tool-strip");
  if (!strip) {
    strip = document.createElement("div");
    strip.className = "tool-strip";
    messageView.body.appendChild(strip);
  }
  return strip;
}

function updateTool(messageView, name, status, success = true) {
  const strip = ensureToolStrip(messageView);
  let pill = [...strip.children].find((item) => item.dataset.tool === name);
  if (!pill) {
    pill = document.createElement("span");
    pill.className = "tool-pill";
    pill.dataset.tool = name;
    strip.appendChild(pill);
  }
  pill.textContent = `${name} · ${status}`;
  pill.classList.toggle("done", status === "完成" && success);
  pill.classList.toggle("failed", status === "完成" && !success);
  scrollChat();
}

function appendResultPanel(messageView, result) {
  messageView.bubble.textContent = result.answer || messageView.bubble.textContent;
  const old = messageView.body.querySelector(".result-panel");
  if (old) old.remove();
  const hasDetails = (
    result.attachments?.length || result.sources?.length || result.generated_files?.length ||
    result.safety_notes?.length || result.error_message
  );
  if (!hasDetails) return;
  const panel = document.createElement("div");
  panel.className = `result-panel ${result.safety_level === "high" ? "safety-high" : ""}`;

  const addList = (title, items, renderer) => {
    if (!items?.length) return;
    const section = document.createElement("section");
    section.className = "result-section";
    const heading = document.createElement("h3");
    heading.textContent = title;
    const list = document.createElement("ul");
    items.forEach((item) => {
      const li = document.createElement("li");
      renderer(li, item);
      list.appendChild(li);
    });
    section.append(heading, list);
    panel.appendChild(section);
  };

  addList("回答依据文件", result.attachments?.filter((item) => item.parse_status === "parsed"), (li, item) => {
    li.textContent = item.filename;
    if (item.summary) li.title = item.summary;
  });
  addList("联网来源", result.sources, (li, source) => {
    if (source.url) {
      const link = document.createElement("a");
      link.href = source.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = source.title || source.url;
      li.appendChild(link);
    } else {
      li.textContent = source.title || "未命名来源";
    }
    if (source.summary) li.append(` — ${source.summary}`);
  });
  addList("生成文件", result.generated_files, (li, file) => {
    li.textContent = `${file.relative_path}（${file.file_type}）`;
  });
  addList("安全提示", result.safety_notes, (li, note) => { li.textContent = note; });
  if (result.error_message) addList("错误信息", [result.error_message], (li, text) => { li.textContent = text; });
  messageView.body.appendChild(panel);
  scrollChat();
}

const attachmentController = new AttachmentController({
  input: dom.attachmentInput,
  button: dom.attachmentButton,
  selectedList: dom.selectedAttachmentList,
  threadBar: dom.threadAttachmentBar,
  threadList: dom.threadAttachmentList,
  maxFiles: maxAttachments,
  maxSizeMb,
  onToast: showToast,
});

attachmentController.setDeleteHandler(async (attachment) => {
  const query = new URLSearchParams({ user_id: state.userId, thread_id: state.threadId });
  const response = await fetch(`/api/chat/attachments/${encodeURIComponent(attachment.attachment_id)}?${query}`, { method: "DELETE" });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(result.detail || "删除附件失败");
  showToast(`${attachment.filename} 已从当前会话移除`);
});

function setBusy(busy) {
  state.streaming = busy;
  dom.sendButton.disabled = busy;
  dom.messageInput.disabled = busy;
  dom.newSessionButton.disabled = busy;
  attachmentController.setDisabled(busy);
}

async function uploadComposerAttachments() {
  if (!attachmentController.hasPending()) return [];
  setProcess("正在上传附件…");
  const results = await attachmentController.uploadPending(state.userId, state.threadId, (packet) => {
    const data = packet.data || {};
    if (packet.event === "upload_start") setProcess(`正在上传 ${data.filename}…`);
    if (packet.event === "parse_start" || packet.event === "parse_progress") setProcess(`正在解析 ${data.filename || "文件"}…`);
    if (packet.event === "tool_start") setProcess("MinerU / 本地解析工具正在工作…");
    if (packet.event === "error") showToast(data.message || "附件处理失败");
  });
  const parsed = results.filter((item) => item.parse_status === "parsed");
  if (!parsed.length) throw new Error("附件未能成功解析，请检查格式、配置或重新上传。");
  attachmentController.addActive(parsed);
  return results;
}

async function streamAnswer(message, attachmentIds = []) {
  const assistant = createMessage("assistant", "");
  let receivedToken = false;
  const response = await fetch("/api/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message,
      user_id: state.userId,
      thread_id: state.threadId,
      attachment_ids: attachmentIds,
    }),
  });
  await parseNdjson(response, (packet) => {
    const data = packet.data || {};
    if (packet.event === "token") {
      if (!receivedToken) assistant.bubble.textContent = "";
      receivedToken = true;
      assistant.bubble.textContent += data.text || "";
      setProcess("化实正在生成回答…");
      scrollChat();
    } else if (packet.event === "tool_start") {
      updateTool(assistant, data.name || "tool", "运行中");
      setProcess(`正在调用 ${data.name || "工具"}…`);
    } else if (packet.event === "tool_end") {
      updateTool(assistant, data.name || "tool", "完成", data.success !== false);
    } else if (packet.event === "result") {
      appendResultPanel(assistant, data);
      if (data.attachments) attachmentController.addActive(data.attachments);
    } else if (packet.event === "error") {
      assistant.bubble.textContent = data.message || "请求处理失败。";
      assistant.bubble.dataset.error = "true";
      showToast(data.message || "请求处理失败。");
    }
  });
}

async function sendMessage(message) {
  if (state.streaming) return;
  heartbeat.start();
  persistIdentity();
  if (!state.threadId) await resetSession(false);
  const originalMessage = message.trim();
  if (!originalMessage && !attachmentController.hasPending()) {
    showToast("请输入问题或选择附件");
    return;
  }

  setBusy(true);
  let uploaded = [];
  try {
    if (attachmentController.hasPending()) uploaded = await uploadComposerAttachments();
    const parsed = uploaded.filter((item) => item.parse_status === "parsed");
    const displayMessage = originalMessage || "请告诉我你希望针对该文件进行总结、提取、问答还是其他处理。";
    createMessage("user", displayMessage, uploaded);
    dom.messageInput.value = "";
    attachmentController.clearPending();
    setProcess("化实正在思考…");
    await streamAnswer(displayMessage, parsed.map((item) => item.attachment_id));
  } catch (error) {
    const messageText = error instanceof Error ? error.message : "请求处理失败";
    showToast(messageText);
    setProcess(messageText);
  } finally {
    setBusy(false);
    setTimeout(() => setProcess("", false), 900);
    dom.messageInput.focus();
    scrollChat();
  }
}

async function loadThreadAttachments() {
  if (!state.threadId) return;
  const query = new URLSearchParams({ user_id: state.userId, thread_id: state.threadId });
  const response = await fetch(`/api/chat/attachments?${query}`);
  if (!response.ok) return;
  const result = await response.json();
  attachmentController.setActive(result.attachments || []);
}

async function resetSession(clearMessages = true) {
  const previousThread = state.threadId;
  persistIdentity();
  const response = await fetch("/api/reset", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_id: state.userId, thread_id: previousThread || null }),
  });
  if (!response.ok) throw new Error("无法创建新会话");
  const result = await response.json();
  state.threadId = result.thread_id;
  persistIdentity();
  attachmentController.clearPending();
  attachmentController.clearActive();
  heartbeat.reset();
  if (clearMessages) {
    dom.messageList.querySelectorAll(".message:not(.welcome-message)").forEach((node) => node.remove());
    showToast("已创建新的独立会话，旧附件不会继承");
  }
}

async function checkHealth() {
  try {
    const response = await fetch("/api/health");
    const result = await response.json();
    if (!response.ok) throw new Error();
    dom.healthBadge.className = "health-badge ok";
    dom.healthBadge.lastChild.textContent = result.capabilities.model ? "模型服务可用" : "基础服务可用 · 模型未配置";
  } catch {
    dom.healthBadge.className = "health-badge error";
    dom.healthBadge.lastChild.textContent = "服务连接异常";
  }
}

dom.chatForm.addEventListener("submit", (event) => {
  event.preventDefault();
  sendMessage(dom.messageInput.value);
});

dom.messageInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    dom.chatForm.requestSubmit();
  }
});

dom.newSessionButton.addEventListener("click", async () => {
  if (state.streaming) return showToast("请等待当前回答结束后再新建会话");
  try { await resetSession(true); } catch (error) { showToast(error.message || "重置失败"); }
});

dom.userIdInput.addEventListener("change", async () => {
  if (state.streaming) return;
  const previousUser = state.userId;
  const previousThread = state.threadId;
  persistIdentity();
  if (state.userId !== previousUser) {
    state.threadId = "";
    localStorage.removeItem("huashi-thread-id");
    attachmentController.clearActive();
    try {
      const response = await fetch("/api/reset", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: state.userId }),
      });
      if (!response.ok) throw new Error();
      state.threadId = (await response.json()).thread_id;
      persistIdentity();
      heartbeat.reset();
      dom.messageList.querySelectorAll(".message:not(.welcome-message)").forEach((node) => node.remove());
      if (previousThread) console.debug("Previous thread detached", previousThread);
    } catch { showToast("更新用户后创建会话失败"); }
  }
});

document.querySelectorAll(".quick-prompts button").forEach((button) => {
  button.addEventListener("click", () => {
    dom.messageInput.value = button.textContent.trim();
    dom.messageInput.focus();
  });
});

dom.openWriteFileButton.addEventListener("click", () => dom.writeFileDialog.showModal());
dom.closeWriteFileButton.addEventListener("click", () => dom.writeFileDialog.close());
dom.cancelWriteFileButton.addEventListener("click", () => dom.writeFileDialog.close());

document.addEventListener("visibilitychange", () => {
  if (document.hidden) heartbeat.pause();
  else heartbeat.resume();
});
window.addEventListener("offline", () => heartbeat.pause());
window.addEventListener("online", () => heartbeat.resume());
window.addEventListener("pagehide", () => heartbeat.stop({ clearSession: true }));

dom.writeFileForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const filename = document.querySelector("#fileNameInput").value.trim();
  const content = document.querySelector("#fileContentInput").value;
  const extension = filename.includes(".") ? filename.split(".").pop().toLowerCase() : "md";
  if (!["md", "txt", "json"].includes(extension)) return setStatus(dom.writeFileStatus, "仅支持 md、txt、json。", "error");
  setStatus(dom.writeFileStatus, "正在保存…");
  try {
    const response = await fetch("/api/write-file", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename, content, file_format: extension, overwrite: false }),
    });
    const result = await response.json();
    if (!result.success) throw new Error(result.error_message || "写入失败");
    setStatus(dom.writeFileStatus, `已保存：${result.relative_path}`, "success");
  } catch (error) {
    setStatus(dom.writeFileStatus, error.message || "写入失败", "error");
  }
});

(async function bootstrap() {
  dom.userIdInput.value = state.userId;
  persistIdentity();
  await checkHealth();
  if (!state.threadId) {
    try { await resetSession(false); } catch { showToast("初始化会话失败，请刷新页面"); }
  } else {
    await loadThreadAttachments();
  }
  heartbeat.start();
  dom.messageInput.focus();
})();
