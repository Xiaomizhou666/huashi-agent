/** 管理 Web 页面轻量心跳；不接触聊天流、Agent 或会话记忆。 */
export class HeartbeatController {
  constructor({
    enabled,
    intervalSeconds,
    onMessage,
    onError = () => {},
    fetchImpl = (...args) => globalThis.fetch(...args),
    setTimeoutImpl = globalThis.setTimeout.bind(globalThis),
    clearTimeoutImpl = globalThis.clearTimeout.bind(globalThis),
    now = () => Date.now(),
    isVisible = () => !globalThis.document?.hidden,
  }) {
    this.enabled = Boolean(enabled);
    this.intervalMs = Math.max(1000, Number(intervalSeconds || 120) * 1000);
    this.onMessage = onMessage;
    this.onError = onError;
    this.fetchImpl = fetchImpl;
    this.setTimeoutImpl = setTimeoutImpl;
    this.clearTimeoutImpl = clearTimeoutImpl;
    this.now = now;
    this.isVisible = isVisible;
    this.timerId = null;
    this.abortController = null;
    this.startedAtMs = null;
    this.sequence = 0;
    this.active = false;
    this.generation = 0;
  }

  /** 启动单个计时器；重复调用不会创建重复任务。 */
  start({ reset = false } = {}) {
    if (!this.enabled) return false;
    if (reset || this.startedAtMs === null) {
      this.startedAtMs = this.now();
      this.sequence = 0;
    }
    if (this.active || !this.isVisible()) return false;
    this.active = true;
    this._schedule();
    return true;
  }

  /** 新会话从零重新计时。 */
  reset() {
    this.stop({ clearSession: true });
    return this.start({ reset: true });
  }

  /** 页面隐藏时暂停网络与定时器，但保留本次会话起点。 */
  pause() {
    this._cancelPending();
    this.active = false;
    this.generation += 1;
  }

  /** 页面重新可见时恢复同一会话计时。 */
  resume() {
    if (!this.enabled || this.startedAtMs === null || !this.isVisible()) return false;
    if (this.active) return false;
    this.active = true;
    this._schedule();
    return true;
  }

  /** 离开页面时彻底停止；可选择清空会话起点。 */
  stop({ clearSession = false } = {}) {
    this._cancelPending();
    this.active = false;
    this.generation += 1;
    if (clearSession) {
      this.startedAtMs = null;
      this.sequence = 0;
    }
  }

  _cancelPending() {
    if (this.timerId !== null) {
      this.clearTimeoutImpl(this.timerId);
      this.timerId = null;
    }
    if (this.abortController) {
      this.abortController.abort();
      this.abortController = null;
    }
  }

  _schedule() {
    if (!this.active || !this.enabled || !this.isVisible()) return;
    if (this.timerId !== null) this.clearTimeoutImpl(this.timerId);
    this.timerId = this.setTimeoutImpl(() => {
      this.timerId = null;
      void this._tick();
    }, this.intervalMs);
  }

  async _tick() {
    if (!this.active || !this.enabled || !this.isVisible() || this.startedAtMs === null) return;
    const generation = this.generation;
    const controller = new AbortController();
    this.abortController = controller;
    const query = new URLSearchParams({
      session_started_at_ms: String(this.startedAtMs),
      sequence: String(this.sequence),
    });
    try {
      const response = await this.fetchImpl(`/api/heartbeat?${query}`, {
        signal: controller.signal,
        headers: { Accept: "application/json" },
      });
      if (generation !== this.generation || !this.active) return;
      if (response.status === 204) {
        this.stop();
        return;
      }
      if (!response.ok) throw new Error(`Heartbeat request failed (${response.status})`);
      const payload = await response.json();
      if (payload.message) this.onMessage(payload.message, payload);
      this.sequence += 1;
    } catch (error) {
      if (error?.name !== "AbortError") this.onError(error);
    } finally {
      if (generation === this.generation) {
        this.abortController = null;
        this._schedule();
      }
    }
  }

  /** 只用于测试与轻量诊断，不暴露任何聊天内容。 */
  snapshot() {
    return {
      enabled: this.enabled,
      active: this.active,
      hasTimer: this.timerId !== null,
      startedAtMs: this.startedAtMs,
      sequence: this.sequence,
    };
  }
}
