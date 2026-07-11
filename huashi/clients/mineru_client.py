"""封装 MinerU v4 本地文件上传、轮询、下载和安全解压流程。"""

from __future__ import annotations

import shutil
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx


class MinerUAPIError(RuntimeError):
    """MinerU 返回失败状态或不可恢复响应。"""


@dataclass(frozen=True)
class MinerUArtifact:
    """MinerU 成功解析后落盘的最小结果。"""

    title: str
    markdown_path: Path
    markdown_text: str


class MinerUClient:
    """MinerU 精准解析 API 客户端。

    流程：申请 v4 上传 URL、PUT 本地文件、轮询 batch 结果、下载 ZIP、
    安全解压并定位 full.md。
    """

    def __init__(
        self,
        token: str,
        *,
        base_url: str = "https://mineru.net",
        timeout_seconds: float = 30.0,
        poll_interval_seconds: float = 3.0,
        max_poll_attempts: int = 40,
        model_version: str = "vlm",
        max_download_mb: int = 200,
        http_client: httpx.Client | None = None,
        sleep_fn: Any = time.sleep,
    ) -> None:
        if not token:
            raise ValueError("MINERU_API_TOKEN 未配置")
        self._token = token
        self.base_url = base_url.rstrip("/")
        self.poll_interval_seconds = poll_interval_seconds
        self.max_poll_attempts = max_poll_attempts
        self.model_version = model_version
        self.max_download_bytes = max_download_mb * 1024 * 1024
        self._client = http_client or httpx.Client(
            timeout=httpx.Timeout(timeout_seconds), follow_redirects=True
        )
        self._owns_client = http_client is None
        self._sleep = sleep_fn

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    def close(self) -> None:
        """关闭内部 HTTP 客户端。"""

        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "MinerUClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _json(self, response: httpx.Response) -> dict[str, Any]:
        try:
            response.raise_for_status()
            payload = response.json()
        except httpx.TimeoutException as exc:
            raise TimeoutError("MinerU 请求超时") from exc
        except httpx.HTTPError as exc:
            raise ConnectionError("MinerU 网络请求失败") from exc
        except ValueError as exc:
            raise MinerUAPIError("MinerU 返回了无法解析的响应") from exc
        if payload.get("code") != 0:
            message = str(payload.get("msg") or "未知错误")
            raise MinerUAPIError(f"MinerU 接口失败：{message[:300]}")
        return payload

    def request_upload_url(self, file_path: Path) -> tuple[str, str]:
        """为一个本地文件申请上传 URL 和 batch_id。"""

        payload = {
            "files": [{"name": file_path.name, "data_id": uuid4().hex}],
            "model_version": self.model_version,
        }
        response = self._client.post(
            f"{self.base_url}/api/v4/file-urls/batch",
            headers=self._headers,
            json=payload,
        )
        data = self._json(response).get("data") or {}
        urls = data.get("file_urls") or []
        batch_id = data.get("batch_id")
        if not batch_id or len(urls) != 1:
            raise MinerUAPIError("MinerU 未返回有效上传地址")
        return str(batch_id), str(urls[0])

    def upload_file(self, upload_url: str, file_path: Path) -> None:
        """PUT 上传文件；按官方要求不额外设置 Content-Type。"""

        try:
            with file_path.open("rb") as stream:
                response = self._client.put(upload_url, content=stream)
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise TimeoutError("MinerU 文件上传超时") from exc
        except httpx.HTTPError as exc:
            raise ConnectionError("MinerU 文件上传失败") from exc

    def poll_batch(self, batch_id: str, expected_name: str) -> str:
        """有限次数轮询批量任务并返回结果 ZIP URL。"""

        endpoint = f"{self.base_url}/api/v4/extract-results/batch/{batch_id}"
        for attempt in range(self.max_poll_attempts):
            response = self._client.get(endpoint, headers=self._headers)
            data = self._json(response).get("data") or {}
            results = data.get("extract_result") or []
            item = next(
                (entry for entry in results if entry.get("file_name") == expected_name),
                results[0] if len(results) == 1 else None,
            )
            if item:
                state = item.get("state")
                if state == "done":
                    url = item.get("full_zip_url")
                    if not url:
                        raise MinerUAPIError("MinerU 任务完成但缺少结果地址")
                    return str(url)
                if state == "failed":
                    reason = str(item.get("err_msg") or "解析失败")
                    raise MinerUAPIError(f"MinerU 解析失败：{reason[:300]}")
            if attempt < self.max_poll_attempts - 1:
                self._sleep(self.poll_interval_seconds)
        raise TimeoutError("MinerU 任务在最大轮询次数内未完成")

    @staticmethod
    def _safe_extract(archive: zipfile.ZipFile, destination: Path) -> None:
        """拒绝 ZIP Slip 路径穿越。"""

        root = destination.resolve()
        for member in archive.infolist():
            target = (destination / member.filename).resolve()
            if target != root and root not in target.parents:
                raise MinerUAPIError("MinerU 结果压缩包包含不安全路径")
        archive.extractall(destination)

    def download_and_extract(self, zip_url: str, destination: Path) -> Path:
        """下载结果 ZIP、限制大小、安全解压并返回 Markdown 路径。"""

        destination.mkdir(parents=True, exist_ok=True)
        zip_path = destination / "mineru-result.zip"
        total = 0
        try:
            with self._client.stream("GET", zip_url) as response:
                response.raise_for_status()
                with zip_path.open("wb") as output:
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > self.max_download_bytes:
                            raise MinerUAPIError("MinerU 结果文件超过下载大小限制")
                        output.write(chunk)
        except httpx.TimeoutException as exc:
            raise TimeoutError("MinerU 结果下载超时") from exc
        except httpx.HTTPError as exc:
            raise ConnectionError("MinerU 结果下载失败") from exc

        try:
            with zipfile.ZipFile(zip_path) as archive:
                self._safe_extract(archive, destination)
        except zipfile.BadZipFile as exc:
            raise MinerUAPIError("MinerU 返回的结果不是有效 ZIP") from exc
        finally:
            zip_path.unlink(missing_ok=True)

        candidates = sorted(destination.rglob("full.md"))
        if not candidates:
            candidates = sorted(destination.rglob("*.md"))
        if not candidates:
            raise MinerUAPIError("MinerU 结果中未找到 Markdown 文件")
        return candidates[0]

    def parse_file(self, file_path: Path, parsed_root: Path) -> MinerUArtifact:
        """执行完整本地文件解析流程。"""

        batch_id, upload_url = self.request_upload_url(file_path)
        self.upload_file(upload_url, file_path)
        result_url = self.poll_batch(batch_id, file_path.name)
        destination = parsed_root / f"{file_path.stem}-{batch_id[:8]}"
        markdown_path = self.download_and_extract(result_url, destination)
        text = markdown_path.read_text(encoding="utf-8", errors="replace")
        return MinerUArtifact(
            title=file_path.stem,
            markdown_path=markdown_path,
            markdown_text=text,
        )


class FakeMinerUClient:
    """离线测试用 MinerU Fake，不需要网络或 Token。"""

    def __init__(
        self,
        markdown_text: str = "# Fake MinerU\n\n离线解析结果。",
        *,
        failure: Exception | None = None,
    ) -> None:
        self.markdown_text = markdown_text
        self.failure = failure

    def parse_file(self, file_path: Path, parsed_root: Path) -> MinerUArtifact:
        """写入确定性 Markdown，或抛出预设异常。"""

        if self.failure:
            raise self.failure
        destination = parsed_root / f"{file_path.stem}-fake"
        destination.mkdir(parents=True, exist_ok=True)
        markdown_path = destination / "full.md"
        markdown_path.write_text(self.markdown_text, encoding="utf-8")
        return MinerUArtifact(file_path.stem, markdown_path, self.markdown_text)
