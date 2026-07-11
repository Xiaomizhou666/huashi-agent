"""安全读取本地文本，并通过独立 MinerU Client 解析复杂文档。"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from langchain_core.tools import StructuredTool

from huashi.clients.mineru_client import MinerUArtifact
from huashi.models import DocumentParseResult

_TEXT_EXTENSIONS = {".txt", ".md"}
_MINERU_EXTENSIONS = {
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".jp2",
    ".webp",
    ".gif",
    ".bmp",
    ".doc",
    ".docx",
    ".ppt",
    ".pptx",
    ".xls",
    ".xlsx",
}
_ALLOWED_EXTENSIONS = _TEXT_EXTENSIONS | _MINERU_EXTENSIONS


class MinerUClientLike(Protocol):
    """文档解析器依赖的最小 MinerU Client 协议。"""

    def parse_file(self, file_path: Path, parsed_root: Path) -> MinerUArtifact:
        """解析一个本地文件。"""


def _summarize(text: str, limit: int = 1200) -> str:
    """不调用模型的安全截断摘要。"""

    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[:limit].rstrip() + "……"


class DocumentParser:
    """限制输入目录、文件类型和大小的本地文档解析器。"""

    def __init__(
        self,
        inputs_dir: Path,
        parsed_dir: Path,
        workspace_dir: Path,
        *,
        max_file_size_mb: int = 20,
        mineru_client: MinerUClientLike | None = None,
        excerpt_chars: int = 6000,
    ) -> None:
        self.inputs_dir = inputs_dir
        self.parsed_dir = parsed_dir
        self.workspace_dir = workspace_dir
        self.max_file_size_bytes = max_file_size_mb * 1024 * 1024
        self.mineru_client = mineru_client
        self.excerpt_chars = excerpt_chars

    def _resolve(self, file_path: str) -> Path:
        candidate = Path(file_path)
        if not candidate.is_absolute():
            candidate = self.inputs_dir / candidate
        resolved = candidate.resolve()
        root = self.inputs_dir.resolve()
        if resolved != root and root not in resolved.parents:
            raise ValueError("只能读取 workspace/inputs 目录内的文件")
        if not resolved.exists() or not resolved.is_file():
            raise FileNotFoundError("文件不存在")
        if resolved.is_symlink():
            raise ValueError("不允许读取符号链接")
        return resolved

    def parse(self, file_path: str) -> DocumentParseResult:
        """读取文本或调用 MinerU；仅返回摘要和截断内容。"""

        try:
            path = self._resolve(file_path)
            suffix = path.suffix.lower()
            if suffix not in _ALLOWED_EXTENSIONS:
                raise ValueError("不支持的文件类型")
            if path.stat().st_size <= 0:
                raise ValueError("文件为空")
            if path.stat().st_size > self.max_file_size_bytes:
                raise ValueError("文件大小超过项目限制")

            if suffix in _TEXT_EXTENSIONS:
                text = path.read_text(encoding="utf-8", errors="replace")
                return DocumentParseResult(
                    success=True,
                    status="done",
                    title=path.stem,
                    summary=_summarize(text),
                    content_excerpt=text[: self.excerpt_chars],
                    result_path=path.relative_to(self.workspace_dir.resolve()).as_posix(),
                )

            if self.mineru_client is None:
                return DocumentParseResult(
                    success=False,
                    status="unavailable",
                    title=path.stem,
                    error_message="该文件类型需要 MINERU_API_TOKEN；当前未配置",
                )
            artifact = self.mineru_client.parse_file(path, self.parsed_dir)
            relative = artifact.markdown_path.resolve().relative_to(
                self.workspace_dir.resolve()
            )
            return DocumentParseResult(
                success=True,
                status="done",
                title=artifact.title,
                summary=_summarize(artifact.markdown_text),
                content_excerpt=artifact.markdown_text[: self.excerpt_chars],
                result_path=relative.as_posix(),
            )
        except Exception as exc:
            return DocumentParseResult(
                success=False,
                status="failed",
                error_message=f"文档解析失败：{str(exc)[:500]}",
            )


def parse_local_document(
    file_path: str, *, parser: DocumentParser
) -> DocumentParseResult:
    """公开的稳定文档解析接口。"""

    return parser.parse(file_path)


def build_document_parser_tool(parser: DocumentParser) -> StructuredTool:
    """构建 Agent 文档解析工具。"""

    def _parse(file_path: str) -> dict[str, object]:
        """读取 workspace/inputs 内的 txt/md/PDF/Office/图片文件。"""

        result = parser.parse(file_path)
        if not result.success and result.status == "failed":
            raise RuntimeError(result.error_message or "文档解析失败")
        return result.model_dump(mode="json")

    return StructuredTool.from_function(
        func=_parse,
        name="parse_local_document",
        description=(
            "读取 workspace/inputs 内的学习资料。txt/md 直接读取；"
            "PDF、图片和 Office 文件通过 MinerU 解析。"
        ),
    )
