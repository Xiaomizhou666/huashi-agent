"""测试本地文本读取、目录限制以及 Fake MinerU 成功和失败。"""

from pathlib import Path

from huashi.clients.mineru_client import FakeMinerUClient
from huashi.tools.file_reader import DocumentParser


def test_read_text_without_mineru(settings) -> None:
    source = settings.inputs_dir / "note.md"
    source.write_text("# 酸碱滴定\n\n终点判断。", encoding="utf-8")
    parser = DocumentParser(
        settings.inputs_dir,
        settings.parsed_dir,
        settings.workspace_dir,
        mineru_client=None,
    )
    result = parser.parse("note.md")
    assert result.success
    assert "酸碱滴定" in (result.summary or "")


def test_reader_blocks_outside_path(settings, tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    parser = DocumentParser(
        settings.inputs_dir,
        settings.parsed_dir,
        settings.workspace_dir,
    )
    result = parser.parse(str(outside))
    assert not result.success
    assert "只能读取" in (result.error_message or "")


def test_complex_document_needs_mineru(settings) -> None:
    (settings.inputs_dir / "paper.pdf").write_bytes(b"fake-pdf")
    parser = DocumentParser(
        settings.inputs_dir,
        settings.parsed_dir,
        settings.workspace_dir,
    )
    result = parser.parse("paper.pdf")
    assert not result.success
    assert result.status == "unavailable"


def test_fake_mineru_parses_offline(settings) -> None:
    (settings.inputs_dir / "paper.pdf").write_bytes(b"fake-pdf")
    parser = DocumentParser(
        settings.inputs_dir,
        settings.parsed_dir,
        settings.workspace_dir,
        mineru_client=FakeMinerUClient("# 解析结果\n\n内容"),
    )
    result = parser.parse("paper.pdf")
    assert result.success
    assert result.result_path and result.result_path.endswith("full.md")


def test_fake_mineru_timeout_is_reported(settings) -> None:
    (settings.inputs_dir / "paper.pdf").write_bytes(b"fake-pdf")
    parser = DocumentParser(
        settings.inputs_dir,
        settings.parsed_dir,
        settings.workspace_dir,
        mineru_client=FakeMinerUClient(failure=TimeoutError("poll timeout")),
    )
    result = parser.parse("paper.pdf")
    assert not result.success
    assert "timeout" in (result.error_message or "")
