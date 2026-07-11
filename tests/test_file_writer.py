"""测试文件写入白名单、路径穿越和默认禁止覆盖。"""

from huashi.tools.file_writer import write_learning_file


def test_write_markdown(settings) -> None:
    result = write_learning_file(
        "氧气实验笔记",
        "# 笔记",
        outputs_dir=settings.outputs_dir,
        workspace_dir=settings.workspace_dir,
    )
    assert result.success
    assert result.relative_path == "outputs/氧气实验笔记.md"


def test_path_traversal_is_rejected(settings) -> None:
    result = write_learning_file(
        "../escape.md",
        "bad",
        outputs_dir=settings.outputs_dir,
        workspace_dir=settings.workspace_dir,
    )
    assert not result.success
    assert not (settings.workspace_dir.parent / "escape.md").exists()


def test_absolute_path_is_rejected(settings) -> None:
    result = write_learning_file(
        "/tmp/escape.md",
        "bad",
        outputs_dir=settings.outputs_dir,
        workspace_dir=settings.workspace_dir,
    )
    assert not result.success


def test_default_does_not_overwrite(settings) -> None:
    first = write_learning_file(
        "report.md",
        "first",
        outputs_dir=settings.outputs_dir,
        workspace_dir=settings.workspace_dir,
    )
    second = write_learning_file(
        "report.md",
        "second",
        outputs_dir=settings.outputs_dir,
        workspace_dir=settings.workspace_dir,
    )
    assert first.success
    assert not second.success
    assert (settings.outputs_dir / "report.md").read_text(encoding="utf-8") == "first"


def test_json_must_be_valid(settings) -> None:
    result = write_learning_file(
        "data.json",
        "{bad json}",
        "json",
        outputs_dir=settings.outputs_dir,
        workspace_dir=settings.workspace_dir,
    )
    assert not result.success
