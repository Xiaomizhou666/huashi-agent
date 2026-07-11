"""验证聊天附件的格式限制、线程绑定、去重、删除和上下文预算。"""

from __future__ import annotations

from pathlib import Path

import pytest

from huashi.attachments import (
    AttachmentAccessError,
    AttachmentError,
    AttachmentManager,
    PendingAttachment,
)
from huashi.document_context import DocumentContextBuilder
from huashi.tools.file_reader import DocumentParser


def _pending(name: str, content: bytes, content_type: str = "text/plain") -> PendingAttachment:
    return PendingAttachment(filename=name, content_type=content_type, content=content)


def test_supported_text_upload_and_local_parse(settings) -> None:
    manager = AttachmentManager(settings)
    stored = manager.store(
        _pending("实验记录.md", "# 滴定\n酚酞终点为浅粉色。".encode()),
        user_id="u1",
        thread_id="t1",
    )
    parser = DocumentParser(
        settings.inputs_dir,
        settings.parsed_dir,
        settings.workspace_dir,
    )
    parsed = manager.parse(
        stored.attachment_id,
        user_id="u1",
        thread_id="t1",
        parser=parser,
    )
    assert parsed.parse_status == "parsed"
    assert "酚酞" in (parsed.summary or "")
    assert not hasattr(parsed, "safe_path")


def test_rejects_unsupported_large_empty_and_traversal(settings) -> None:
    manager = AttachmentManager(settings)
    invalid = [
        _pending("run.py", b"print(1)"),
        _pending("../escape.md", b"bad", "text/markdown"),
        _pending(".env", b"KEY=value"),
        _pending("empty.txt", b""),
        _pending("archive.zip", b"PK", "application/zip"),
        _pending("too-large.txt", b"x" * (settings.max_file_size_mb * 1024 * 1024 + 1)),
    ]
    for item in invalid:
        with pytest.raises(AttachmentError):
            manager.store(item, user_id="u", thread_id="t")
    assert not (settings.workspace_dir.parent / "escape.md").exists()


def test_mime_and_signature_must_match(settings) -> None:
    manager = AttachmentManager(settings)
    with pytest.raises(AttachmentError, match="MIME"):
        manager.store(
            _pending("report.pdf", b"%PDF-fake", "text/plain"),
            user_id="u",
            thread_id="t",
        )
    with pytest.raises(AttachmentError, match="签名"):
        manager.store(
            _pending("report.pdf", b"not-pdf", "application/pdf"),
            user_id="u",
            thread_id="t",
        )


def test_duplicate_file_is_reused_in_same_thread(settings) -> None:
    manager = AttachmentManager(settings)
    pending = _pending("same.txt", b"same content")
    first = manager.store(pending, user_id="u", thread_id="t")
    second = manager.store(pending, user_id="u", thread_id="t")
    assert first.attachment_id == second.attachment_id
    assert second.reused is True
    assert len(manager.list_public(user_id="u", thread_id="t")) == 1


def test_attachment_thread_and_user_isolation(settings) -> None:
    manager = AttachmentManager(settings)
    stored = manager.store(_pending("note.txt", b"thread secret"), user_id="u1", thread_id="t1")
    with pytest.raises(AttachmentAccessError):
        manager.resolve(user_id="u1", thread_id="t2", attachment_ids=[stored.attachment_id])
    with pytest.raises(AttachmentAccessError):
        manager.resolve(user_id="u2", thread_id="t1", attachment_ids=[stored.attachment_id])


def test_delete_and_clear_thread_remove_files(settings) -> None:
    manager = AttachmentManager(settings)
    first = manager.store(_pending("one.txt", b"one"), user_id="u", thread_id="t")
    second = manager.store(_pending("two.txt", b"two"), user_id="u", thread_id="t")
    record_path = manager.resolve(user_id="u", thread_id="t", attachment_ids=[first.attachment_id])[0].safe_path
    assert manager.delete(first.attachment_id, user_id="u", thread_id="t")
    assert not record_path.exists()
    assert manager.clear_thread(user_id="u", thread_id="t") == 1
    assert manager.list_public(user_id="u", thread_id="t") == []
    with pytest.raises(AttachmentAccessError):
        manager.delete(second.attachment_id, user_id="u", thread_id="t")


def test_document_context_has_file_names_and_budget(settings) -> None:
    manager = AttachmentManager(settings)
    parser = DocumentParser(settings.inputs_dir, settings.parsed_dir, settings.workspace_dir)
    ids = []
    for name, text in [("a.txt", "A" * 4000), ("b.txt", "B" * 4000)]:
        stored = manager.store(_pending(name, text.encode()), user_id="u", thread_id="t")
        manager.parse(stored.attachment_id, user_id="u", thread_id="t", parser=parser)
        ids.append(stored.attachment_id)
    records = manager.resolve(user_id="u", thread_id="t", attachment_ids=ids)
    prompt, used = DocumentContextBuilder(max_total_chars=1800, max_file_chars=1200).build("总结", records)
    assert used
    assert "a.txt" in prompt
    assert len(prompt) < 3200
    assert "优先依据" in prompt
