# -*- coding: utf-8 -*-
"""静态检查：index.html 内联 <script> 代码块语法校验（node --check），防止页面脚本低级错误。"""
import re
import subprocess
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "index.html").read_text()


def _inline_scripts():
    blocks = []
    for m in re.finditer(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", HTML, re.S):
        blocks.append(m.group(1))
    return blocks


def test_inline_script_blocks_exist():
    blocks = _inline_scripts()
    assert len(blocks) >= 2, "应至少有主题预处理与主逻辑两个内联脚本"
    assert any("SCENARIOS" in b or "startCampaign" in b for b in blocks)


@pytest.mark.skipif(shutil.which("node") is None, reason="node 不可用")
def test_inline_js_syntax_valid(tmp_path):
    node = shutil.which("node")
    blocks = _inline_scripts()
    for i, code in enumerate(blocks):
        f = tmp_path / f"inline_{i}.js"
        f.write_text(code)
        r = subprocess.run([node, "--check", str(f)], capture_output=True, text=True)
        assert r.returncode == 0, f"内联脚本 #{i} 语法错误:\n{r.stderr[:800]}"


def test_referenced_elements_exist():
    """主逻辑里 $() 引用的所有 id 必须真实存在于 HTML 中。"""
    ids_in_html = set(re.findall(r'id="([^"]+)"', HTML))
    ids_used = set(re.findall(r'\$\("([\w-]+)"\)', HTML))
    missing = ids_used - ids_in_html
    assert not missing, f"JS 引用了不存在的元素 id: {missing}"


def test_engine_exports_covered():
    """index.html 调用的 Engine 方法必须在 game-core.js 中都有定义。"""
    core = (ROOT / "game-core.js").read_text()
    defined = set(re.findall(r"E\.(\w+)\s*=", core))
    used = set(re.findall(r"E\.(\w+)\(", (ROOT / "index.html").read_text()))
    missing = used - defined
    assert not missing, f"index.html 调用了未定义的引擎方法: {missing}"
