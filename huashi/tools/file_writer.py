"""提供限制在 workspace/outputs 的安全学习文件写入工具。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

from langchain_core.tools import StructuredTool

from huashi.models import FileWriteResult

_ALLOWED_FORMATS = {"md", "txt", "json"}
_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def _safe_filename(filename: str, file_format: str) -> str:
    """校验单一文件名并拒绝路径穿越和特殊文件名。"""

    if not filename or len(filename) > 220:
        raise ValueError("文件名为空或过长")
    if Path(filename).is_absolute() or Path(filename).name != filename:
        raise ValueError("文件名不能包含目录、绝对路径或路径穿越")
    if any(ord(ch) < 32 for ch in filename) or "\x00" in filename:
        raise ValueError("文件名包含控制字符")
    if filename.startswith("."):
        raise ValueError("不允许写入隐藏文件")
    if not re.fullmatch(r"[\w\-一-鿿（）() ]+(?:\.[A-Za-z0-9]+)?", filename):
        raise ValueError("文件名包含不安全字符")
    path = Path(filename)
    if path.stem.casefold() in _RESERVED_NAMES:
        raise ValueError("文件名属于系统保留名称")
    suffix = path.suffix.lower().lstrip(".")
    if suffix and suffix != file_format:
        raise ValueError("文件扩展名与 file_format 不一致")
    return filename if suffix else f"{filename}.{file_format}"


def write_learning_file(
    filename: str,
    content: str,
    file_format: Literal["md", "txt", "json"] = "md",
    overwrite: bool = False,
    *,
    outputs_dir: Path = Path("workspace/outputs"),
    workspace_dir: Path = Path("workspace"),
) -> FileWriteResult:
    """将学习内容安全写入 outputs，默认不覆盖已有文件。"""

    try:
        if file_format not in _ALLOWED_FORMATS:
            raise ValueError("仅支持 md、txt、json")
        safe_name = _safe_filename(filename, file_format)
        outputs_dir.mkdir(parents=True, exist_ok=True)
        root = outputs_dir.resolve()
        target = (outputs_dir / safe_name).resolve()
        if root not in target.parents:
            raise ValueError("目标路径超出允许目录")
        if target.exists() and not overwrite:
            return FileWriteResult(
                success=False,
                file_type=file_format,
                error_message="文件已存在；默认禁止覆盖",
            )
        if file_format == "json":
            parsed = json.loads(content)
            content_to_write = json.dumps(parsed, ensure_ascii=False, indent=2)
        else:
            content_to_write = content
        target.write_text(content_to_write, encoding="utf-8")
        relative = target.relative_to(workspace_dir.resolve()).as_posix()
        return FileWriteResult(
            success=True, relative_path=relative, file_type=file_format
        )
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        return FileWriteResult(success=False, error_message=str(exc))


def build_file_writer_tool(outputs_dir: Path, workspace_dir: Path) -> StructuredTool:
    """构建绑定项目目录的 LangChain 文件写入工具。"""

    def _write(
        filename: str,
        content: str,
        file_format: Literal["md", "txt", "json"] = "md",
        overwrite: bool = False,
    ) -> dict[str, object]:
        """保存学习笔记、实验报告草稿或知识总结。"""

        return write_learning_file(
            filename,
            content,
            file_format,
            overwrite,
            outputs_dir=outputs_dir,
            workspace_dir=workspace_dir,
        ).model_dump(mode="json")

    return StructuredTool.from_function(
        func=_write,
        name="write_learning_file",
        description=(
            "将学习笔记、实验报告草稿或知识总结写入安全目录。"
            "仅支持 md/txt/json，默认不覆盖。"
        ),
    )
