/** 管理聊天输入区的待上传文件、线程附件与解析状态。 */
import { parseNdjson } from "./stream.js";

const ALLOWED_EXTENSIONS = new Set([
  "pdf", "doc", "docx", "ppt", "pptx", "xls", "xlsx",
  "txt", "md", "png", "jpg", "jpeg",
]);

const STATUS_TEXT = {
  waiting_upload: "等待上传",
  uploading: "上传中",
  waiting_parse: "等待解析",
  parsing: "解析中",
  parsed: "已解析",
  failed: "解析失败",
};

function extensionOf(name) {
  const parts = name.toLowerCase().split(".");
  return parts.length > 1 ? parts.pop() : "";
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function fileKey(file) {
  return `${file.name}:${file.size}:${file.lastModified}`;
}

export function renderMessageAttachments(container, attachments = []) {
  if (!attachments.length) return;
  const wrapper = document.createElement("div");
  wrapper.className = "message-attachments";
  attachments.forEach((attachment) => {
    const card = document.createElement("div");
    card.className = `message-attachment ${attachment.parse_status === "failed" ? "failed" : ""}`;
    const icon = document.createElement("span");
    icon.className = "file-type-icon";
    icon.textContent = (attachment.file_type || extensionOf(attachment.filename) || "FILE").toUpperCase().slice(0, 4);
    const body = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = attachment.filename;
    const meta = document.createElement("span");
    meta.textContent = `${formatBytes(attachment.file_size || 0)} · ${STATUS_TEXT[attachment.parse_status] || attachment.parse_status}`;
    body.append(title, meta);
    card.append(icon, body);
    wrapper.appendChild(card);
  });
  container.appendChild(wrapper);
}

export class AttachmentController {
  constructor({ input, button, selectedList, threadBar, threadList, maxFiles, maxSizeMb, onToast }) {
    this.input = input;
    this.button = button;
    this.selectedList = selectedList;
    this.threadBar = threadBar;
    this.threadList = threadList;
    this.maxFiles = maxFiles;
    this.maxSizeBytes = maxSizeMb * 1024 * 1024;
    this.onToast = onToast;
    this.pending = [];
    this.active = new Map();
    this.onDelete = null;
    this.disabled = false;
    this.button.addEventListener("click", () => this.input.click());
    this.input.addEventListener("change", () => this.addFiles([...this.input.files]));
  }

  setDeleteHandler(handler) {
    this.onDelete = handler;
  }

  addFiles(files) {
    for (const file of files) {
      if (this.pending.length >= this.maxFiles) {
        this.onToast(`单次最多选择 ${this.maxFiles} 个文件`);
        break;
      }
      const error = this.validateFile(file);
      if (error) {
        this.onToast(error);
        continue;
      }
      if (this.pending.some((entry) => entry.key === fileKey(file))) {
        this.onToast(`${file.name} 已在待发送列表中`);
        continue;
      }
      this.pending.push({ key: fileKey(file), file, status: "waiting_upload", message: "等待上传" });
    }
    this.input.value = "";
    this.renderPending();
  }

  validateFile(file) {
    if (!file.name || file.name.length > 180 || file.name.startsWith(".") || file.name.includes("..") || /[\\/]/.test(file.name)) {
      return "文件名为空、过长或包含不安全路径字符";
    }
    const extension = extensionOf(file.name);
    if (!ALLOWED_EXTENSIONS.has(extension)) return `${file.name} 的格式不受支持`;
    if (!file.size) return `${file.name} 是空文件`;
    if (file.size > this.maxSizeBytes) return `${file.name} 超过大小限制`;
    return "";
  }

  hasPending() {
    return this.pending.length > 0;
  }

  setDisabled(disabled) {
    this.disabled = disabled;
    this.button.disabled = disabled;
    this.renderPending();
    this.renderActive();
  }

  renderPending() {
    this.selectedList.replaceChildren();
    this.selectedList.hidden = this.pending.length === 0;
    this.pending.forEach((entry) => {
      const card = document.createElement("div");
      card.className = `selected-file status-${entry.status}`;
      card.dataset.key = entry.key;
      const type = document.createElement("span");
      type.className = "file-type-icon";
      type.textContent = extensionOf(entry.file.name).toUpperCase().slice(0, 4);
      const info = document.createElement("div");
      const name = document.createElement("strong");
      name.textContent = entry.file.name;
      const meta = document.createElement("span");
      meta.textContent = `${formatBytes(entry.file.size)} · ${entry.message}`;
      info.append(name, meta);
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "remove-file";
      remove.setAttribute("aria-label", `移除 ${entry.file.name}`);
      remove.textContent = "×";
      remove.disabled = this.disabled;
      remove.addEventListener("click", () => {
        this.pending = this.pending.filter((item) => item.key !== entry.key);
        this.renderPending();
      });
      card.append(type, info, remove);
      this.selectedList.appendChild(card);
    });
  }

  updatePending(filename, status, message) {
    const entry = this.pending.find((item) => item.file.name === filename);
    if (!entry) return;
    entry.status = status;
    entry.message = message || STATUS_TEXT[status] || status;
    this.renderPending();
  }

  async uploadPending(userId, threadId, onEvent = () => {}) {
    if (!this.pending.length) return [];
    const form = new FormData();
    form.append("user_id", userId);
    form.append("thread_id", threadId);
    this.pending.forEach((entry) => form.append("files", entry.file));
    const response = await fetch("/api/chat/attachments", { method: "POST", body: form });
    let finalAttachments = [];
    await parseNdjson(response, (packet) => {
      const data = packet.data || {};
      if (packet.event === "upload_start") this.updatePending(data.filename, "uploading", "上传中");
      if (packet.event === "upload_end") this.updatePending(data.filename, "waiting_parse", data.reused ? "已存在，正在复用" : "上传完成");
      if (packet.event === "parse_start" || packet.event === "parse_progress") this.updatePending(data.filename, "parsing", data.message || "解析中");
      if (packet.event === "parse_end") this.updatePending(data.filename, data.success ? "parsed" : "failed", data.success ? "已解析" : (data.error_message || "解析失败"));
      if (packet.event === "error" && data.filename) this.updatePending(data.filename, "failed", data.message || "处理失败");
      if (packet.event === "result") finalAttachments = data.attachments || [];
      onEvent(packet);
    });
    return finalAttachments;
  }

  clearPending() {
    this.pending = [];
    this.renderPending();
  }

  setActive(attachments) {
    this.active.clear();
    attachments.filter((item) => item.parse_status === "parsed").forEach((item) => this.active.set(item.attachment_id, item));
    this.renderActive();
  }

  addActive(attachments) {
    attachments.filter((item) => item.parse_status === "parsed").forEach((item) => this.active.set(item.attachment_id, item));
    this.renderActive();
  }

  clearActive() {
    this.active.clear();
    this.renderActive();
  }

  renderActive() {
    this.threadList.replaceChildren();
    this.threadBar.hidden = this.active.size === 0;
    this.active.forEach((attachment) => {
      const chip = document.createElement("div");
      chip.className = "thread-file-chip";
      const label = document.createElement("span");
      label.textContent = attachment.filename;
      label.title = attachment.summary || attachment.filename;
      const remove = document.createElement("button");
      remove.type = "button";
      remove.setAttribute("aria-label", `从会话中移除 ${attachment.filename}`);
      remove.textContent = "×";
      remove.disabled = this.disabled;
      remove.addEventListener("click", async () => {
        if (!this.onDelete) return;
        remove.disabled = true;
        try {
          await this.onDelete(attachment);
          this.active.delete(attachment.attachment_id);
          this.renderActive();
        } catch (error) {
          remove.disabled = false;
          this.onToast(error instanceof Error ? error.message : "删除附件失败");
        }
      });
      chip.append(label, remove);
      this.threadList.appendChild(chip);
    });
  }
}
