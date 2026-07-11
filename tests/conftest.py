"""共享临时配置和测试夹具。"""

from pathlib import Path

import pytest

from huashi.config import HuashiSettings


@pytest.fixture
def settings(tmp_path: Path) -> HuashiSettings:
    """创建不含任何真实密钥的临时项目配置。"""

    value = HuashiSettings(
        WORKSPACE_DIR=tmp_path / "workspace",
        MAX_FILE_SIZE_MB=2,
        LANGSMITH_TRACING=False,
    )
    value.ensure_workspace()
    return value
