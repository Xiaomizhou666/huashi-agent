"""启动“化实”交互式命令行，复用 HuashiService 业务接口。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from huashi.config import HuashiSettings
from huashi.models import AssistantResponse, DocumentParseResult, FileWriteResult
from huashi.service import HuashiService

HELP_TEXT = """
可用命令：
  help                         显示帮助
  reset                        创建新的 thread_id
  read <文件名>                读取 workspace/inputs 内文件
  write <文件名> | <内容>      写入 workspace/outputs（按扩展名识别格式）
  quit                         退出
其他输入会作为普通问题发送给“化实”。
""".strip()


def _format_response(response: AssistantResponse) -> str:
    lines = [response.answer]
    if response.tools_used:
        lines.append("工具：" + "、".join(response.tools_used))
    if response.sources:
        lines.append("来源：")
        for index, source in enumerate(response.sources, start=1):
            url = f" - {source.url}" if source.url else ""
            lines.append(f"  {index}. {source.title}{url}")
    if response.generated_files:
        lines.append("文件：" + "、".join(f.relative_path for f in response.generated_files))
    if response.safety_notes:
        lines.append("安全提示：" + "；".join(response.safety_notes))
    if response.error_message:
        lines.append("错误信息：" + response.error_message)
    return "\n".join(lines)


def _format_file_result(result: FileWriteResult | DocumentParseResult) -> str:
    if isinstance(result, FileWriteResult):
        return (
            f"已保存：{result.relative_path}"
            if result.success
            else f"写入失败：{result.error_message}"
        )
    if result.success:
        return f"标题：{result.title}\n摘要：{result.summary}\n结果：{result.result_path}"
    return f"读取失败：{result.error_message}"


def _parse_write_command(raw: str) -> tuple[str, str, str]:
    if "|" not in raw:
        raise ValueError("格式应为：write 文件名 | 内容")
    filename, content = (part.strip() for part in raw.split("|", 1))
    suffix = Path(filename).suffix.lower().lstrip(".") or "md"
    if suffix not in {"md", "txt", "json"}:
        raise ValueError("write 仅支持 md、txt、json")
    return filename, content, suffix


def run_cli() -> int:
    """运行交互 CLI；工具失败时不输出完整堆栈。"""

    load_dotenv()
    settings = HuashiSettings()
    service = HuashiService(settings)
    user_id = "cli-user"
    thread_id = service.new_thread_id()
    print("化实：化学实验学习智能助手")
    print(f"当前 thread_id: {thread_id}")
    capability = settings.capability_summary()
    if not capability["model"]:
        print("提示：未配置模型，聊天不可用；read/write/help 仍可使用。")
    print("输入 help 查看命令。")

    while True:
        try:
            raw = input("\n你：").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见。")
            return 0
        if not raw:
            continue
        command = raw.casefold()
        try:
            if command == "quit":
                print("再见。")
                return 0
            if command == "help":
                print(HELP_TEXT)
                continue
            if command == "reset":
                thread_id = service.new_thread_id()
                print(f"已创建新会话，thread_id: {thread_id}")
                continue
            if command.startswith("read "):
                print(_format_file_result(service.parse_document(raw[5:].strip())))
                continue
            if command.startswith("write "):
                filename, content, suffix = _parse_write_command(raw[6:].strip())
                print(_format_file_result(service.write_file(filename, content, suffix)))
                continue
            response = service.chat(raw, user_id=user_id, thread_id=thread_id)
            print("\n化实：" + _format_response(response))
            if settings.debug:
                print("DEBUG JSON:")
                print(json.dumps(response.model_dump(mode="json"), ensure_ascii=False, indent=2))
        except Exception as exc:
            print(f"操作失败：{type(exc).__name__}: {str(exc)[:500]}")
            if settings.debug:
                raise


if __name__ == "__main__":
    sys.exit(run_cli())
