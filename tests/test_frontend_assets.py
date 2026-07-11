"""检查前端资源本地化与源码中无明显硬编码密钥。"""

from pathlib import Path
import re


def test_frontend_has_no_external_cdn_or_inline_secret() -> None:
    root = Path(__file__).resolve().parents[1]
    html = (root / "frontend/templates/index.html").read_text(encoding="utf-8")
    assert "cdn." not in html.lower()
    assert "https://" not in html.lower()
    assert "src=\"/static/js/app.js\"" in html
    assert "href=\"/static/css/style.css\"" in html

    source_files = list((root / "huashi").rglob("*.py")) + list(
        (root / "frontend").rglob("*")
    )
    secret_pattern = re.compile(
        r"(?i)(?:api[_-]?key|token|password)\s*=\s*['\"][A-Za-z0-9_\-]{16,}['\"]"
    )
    for path in source_files:
        if path.is_file():
            assert not secret_pattern.search(path.read_text(encoding="utf-8"))


def test_frontend_wires_lightweight_heartbeat() -> None:
    """页面应配置并加载独立心跳控制器，而不是写入聊天请求。"""

    root = Path(__file__).resolve().parents[1]
    html = (root / "frontend/templates/index.html").read_text(encoding="utf-8")
    app_js = (root / "frontend/static/js/app.js").read_text(encoding="utf-8")
    heartbeat_js = (root / "frontend/static/js/heartbeat.js").read_text(encoding="utf-8")
    assert "data-heartbeat-enabled" in html
    assert "data-heartbeat-interval-seconds" in html
    assert 'from "./heartbeat.js"' in app_js
    assert 'visibilitychange' in app_js
    assert 'offline' in app_js and 'online' in app_js
    assert 'pagehide' in app_js
    assert '/api/heartbeat' in heartbeat_js
    assert '/api/chat' not in heartbeat_js
