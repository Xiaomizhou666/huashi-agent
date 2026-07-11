"""管理聊天附件的安全落盘、解析状态、线程绑定与生命周期。"""

from __future__ import annotations

import hashlib
import re
import shutil
import threading
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable
from uuid import uuid4

from huashi.config import HuashiSettings
from huashi.models import AttachmentResult, DocumentParseResult
from huashi.tools.file_reader import DocumentParser

_ALLOWED_MIME_TYPES: dict[str, set[str]] = {
    ".txt": {"text/plain", "application/octet-stream"},
    ".md": {"text/markdown", "text/plain", "application/octet-stream"},
    ".pdf": {"application/pdf", "application/octet-stream"},
    ".doc": {"application/msword", "application/octet-stream"},
    ".docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/octet-stream",
    },
    ".ppt": {"application/vnd.ms-powerpoint", "application/octet-stream"},
    ".pptx": {
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/octet-stream",
    },
    ".xls": {"application/vnd.ms-excel", "application/octet-stream"},
    ".xlsx": {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/octet-stream",
    },
    ".png": {"image/png", "application/octet-stream"},
    ".jpg": {"image/jpeg", "application/octet-stream"},
    ".jpeg": {"image/jpeg", "application/octet-stream"},
}
_SAFE_NAME = re.compile(r"^[\w\-一-鿿（）() .]+$")


class AttachmentError(ValueError):
    """聊天附件业务错误的基类。"""


class AttachmentAccessError(AttachmentError):
    """附件不存在，或不属于当前用户与线程。"""


class DuplicateAttachmentError(AttachmentError):
    """相同内容已存在于当前线程。"""

    def __init__(self, attachment_id: str) -> None:
        super().__init__("同一文件已在当前会话中上传")
        self.attachment_id = attachment_id


@dataclass(frozen=True)
class PendingAttachment:
    """路由层完成读取后交给 Service 的待保存附件。"""

    filename: str
    content_type: str
    content: bytes


@dataclass(frozen=True)
class AttachmentRecord:
    """仅在服务进程内保存的附件记录，包含不对外暴露的安全路径。"""

    attachment_id: str
    user_id: str
    thread_id: str
    filename: str
    file_type: str
    file_size: int
    content_type: str
    sha256: str
    safe_path: Path
    relative_input_path: str
    parse_status: str
    created_at: datetime
    summary: str | None = None
    parsed_text: str | None = None
    result_path: str | None = None
    error_message: str | None = None

    def public(self, *, reused: bool = False) -> AttachmentResult:
        """转换为不包含服务器路径的公开结果。"""

        return AttachmentResult(
            attachment_id=self.attachment_id,
            filename=self.filename,
            file_type=self.file_type,
            file_size=self.file_size,
            parse_status=self.parse_status,
            summary=self.summary,
            error_message=self.error_message,
            created_at=self.created_at,
            reused=reused,
        )


class AttachmentManager:
    """使用内存索引和 workspace 临时目录管理线程级附件。"""

    def __init__(self, settings: HuashiSettings) -> None:
        self.settings = settings
        self.root = settings.inputs_dir / "attachments"
        self.root.mkdir(parents=True, exist_ok=True)
        self._records: dict[str, AttachmentRecord] = {}
        self._thread_index: dict[tuple[str, str], list[str]] = {}
        self._hash_index: dict[tuple[str, str, str], str] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _namespace(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]

    def validate_pending(self, pending: PendingAttachment) -> tuple[str, str]:
        """校验文件名、扩展名、MIME、大小与基础文件签名。"""

        filename = pending.filename.strip()
        if not filename or len(filename) > self.settings.max_filename_length:
            raise AttachmentError("文件名为空或过长")
        path = Path(filename)
        if (
            path.is_absolute()
            or path.name != filename
            or filename.startswith(".")
            or ".." in filename
            or "/" in filename
            or "\\" in filename
        ):
            raise AttachmentError("文件名不能包含路径、隐藏前缀或路径穿越字符")
        if not _SAFE_NAME.fullmatch(filename) or any(ord(ch) < 32 for ch in filename):
            raise AttachmentError("文件名包含不安全字符")

        suffix = path.suffix.lower()
        if suffix not in _ALLOWED_MIME_TYPES:
            raise AttachmentError("不支持该文件类型")
        content_type = (pending.content_type or "application/octet-stream").lower()
        if content_type not in _ALLOWED_MIME_TYPES[suffix]:
            raise AttachmentError("文件 MIME 类型与扩展名不匹配")

        size = len(pending.content)
        if size == 0:
            raise AttachmentError("文件为空")
        if size > self.settings.max_file_size_mb * 1024 * 1024:
            raise AttachmentError(
                f"文件超过 {self.settings.max_file_size_mb} MB 限制"
            )
        self._validate_signature(suffix, pending.content)
        return filename, suffix.lstrip(".")

    @staticmethod
    def _validate_signature(suffix: str, content: bytes) -> None:
        """用轻量魔数检查阻止最明显的扩展名伪装。"""

        if suffix == ".pdf" and not content.startswith(b"%PDF"):
            raise AttachmentError("PDF 文件签名无效")
        if suffix == ".png" and not content.startswith(b"\x89PNG\r\n\x1a\n"):
            raise AttachmentError("PNG 文件签名无效")
        if suffix in {".jpg", ".jpeg"} and not content.startswith(b"\xff\xd8\xff"):
            raise AttachmentError("JPEG 文件签名无效")
        if suffix in {".docx", ".pptx", ".xlsx"} and not content.startswith(b"PK"):
            raise AttachmentError("Office Open XML 文件签名无效")
        if suffix in {".doc", ".ppt", ".xls"} and not content.startswith(
            b"\xd0\xcf\x11\xe0"
        ):
            raise AttachmentError("旧版 Office 文件签名无效")

    def store(
        self,
        pending: PendingAttachment,
        *,
        user_id: str,
        thread_id: str,
    ) -> AttachmentResult:
        """安全保存附件并创建等待解析记录；同内容不会重复落盘。"""

        filename, file_type = self.validate_pending(pending)
        digest = hashlib.sha256(pending.content).hexdigest()
        duplicate_key = (user_id, thread_id, digest)
        with self._lock:
            existing_id = self._hash_index.get(duplicate_key)
            if existing_id and existing_id in self._records:
                return self._records[existing_id].public(reused=True)

        attachment_id = f"att_{uuid4().hex}"
        directory = (
            self.root
            / self._namespace(user_id)
            / self._namespace(thread_id)
            / attachment_id
        ).resolve()
        allowed_root = self.root.resolve()
        if allowed_root not in directory.parents:
            raise AttachmentError("附件保存目录校验失败")
        directory.mkdir(parents=True, exist_ok=False)
        target = (directory / filename).resolve()
        if directory not in target.parents:
            shutil.rmtree(directory, ignore_errors=True)
            raise AttachmentError("附件保存路径校验失败")
        try:
            target.write_bytes(pending.content)
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise

        relative_input = target.relative_to(self.settings.inputs_dir.resolve()).as_posix()
        record = AttachmentRecord(
            attachment_id=attachment_id,
            user_id=user_id,
            thread_id=thread_id,
            filename=filename,
            file_type=file_type,
            file_size=len(pending.content),
            content_type=pending.content_type,
            sha256=digest,
            safe_path=target,
            relative_input_path=relative_input,
            parse_status="waiting_parse",
            created_at=datetime.now(UTC),
        )
        with self._lock:
            self._records[attachment_id] = record
            self._thread_index.setdefault((user_id, thread_id), []).append(attachment_id)
            self._hash_index[duplicate_key] = attachment_id
        return record.public()

    def parse(
        self,
        attachment_id: str,
        *,
        user_id: str,
        thread_id: str,
        parser: DocumentParser,
    ) -> AttachmentResult:
        """使用现有 DocumentParser/MinerU 解析附件并保存受控文本。"""

        record = self._owned_record(attachment_id, user_id, thread_id)
        with self._lock:
            record = replace(record, parse_status="parsing", error_message=None)
            self._records[attachment_id] = record

        result: DocumentParseResult = parser.parse(record.relative_input_path)
        if result.success:
            parsed_text = (result.content_excerpt or result.summary or "").strip()
            if not parsed_text:
                result = DocumentParseResult(
                    success=False,
                    status="failed",
                    title=result.title,
                    error_message="文档解析完成，但未提取到可用文本",
                )

        with self._lock:
            current = self._records[attachment_id]
            if result.success:
                updated = replace(
                    current,
                    parse_status="parsed",
                    summary=result.summary,
                    parsed_text=(result.content_excerpt or result.summary or "").strip(),
                    result_path=result.result_path,
                    error_message=None,
                )
            else:
                updated = replace(
                    current,
                    parse_status="failed",
                    summary=None,
                    parsed_text=None,
                    result_path=result.result_path,
                    error_message=result.error_message or "文件解析失败",
                )
            self._records[attachment_id] = updated
        return updated.public()

    def _owned_record(
        self, attachment_id: str, user_id: str, thread_id: str
    ) -> AttachmentRecord:
        with self._lock:
            record = self._records.get(attachment_id)
        if record is None or record.user_id != user_id or record.thread_id != thread_id:
            raise AttachmentAccessError("附件不存在或不属于当前会话")
        return record

    def resolve(
        self,
        *,
        user_id: str,
        thread_id: str,
        attachment_ids: Iterable[str] | None = None,
    ) -> list[AttachmentRecord]:
        """返回当前线程附件；显式 ID 会进行严格归属校验。"""

        ids = list(attachment_ids or [])
        if ids:
            if len(ids) != len(set(ids)):
                raise AttachmentAccessError("附件列表包含重复 ID")
            return [self._owned_record(item, user_id, thread_id) for item in ids]
        with self._lock:
            thread_ids = list(self._thread_index.get((user_id, thread_id), []))
            return [self._records[item] for item in thread_ids if item in self._records]

    def list_public(self, *, user_id: str, thread_id: str) -> list[AttachmentResult]:
        """列出当前线程附件，不暴露安全路径或解析全文。"""

        return [
            record.public()
            for record in self.resolve(user_id=user_id, thread_id=thread_id)
        ]

    def delete(self, attachment_id: str, *, user_id: str, thread_id: str) -> bool:
        """删除附件文件和内存索引；只能删除当前用户线程的记录。"""

        record = self._owned_record(attachment_id, user_id, thread_id)
        with self._lock:
            self._records.pop(attachment_id, None)
            ids = self._thread_index.get((user_id, thread_id), [])
            self._thread_index[(user_id, thread_id)] = [
                item for item in ids if item != attachment_id
            ]
            self._hash_index.pop((user_id, thread_id, record.sha256), None)
        shutil.rmtree(record.safe_path.parent, ignore_errors=True)
        self._remove_parsed_result(record.result_path)
        return True

    def clear_thread(self, *, user_id: str, thread_id: str) -> int:
        """清理旧线程附件，供显式重置会话时调用。"""

        records = self.resolve(user_id=user_id, thread_id=thread_id)
        count = 0
        for record in records:
            try:
                self.delete(
                    record.attachment_id, user_id=user_id, thread_id=thread_id
                )
                count += 1
            except AttachmentAccessError:
                continue
        return count

    def _remove_parsed_result(self, result_path: str | None) -> None:
        if not result_path:
            return
        try:
            candidate = (self.settings.workspace_dir / result_path).resolve()
            parsed_root = self.settings.parsed_dir.resolve()
            if candidate.is_file() and parsed_root in candidate.parents:
                candidate.unlink(missing_ok=True)
        except OSError:
            return
