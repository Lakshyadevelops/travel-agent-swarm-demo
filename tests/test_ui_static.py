"""Regression guards for the UI's XSS and CSP posture.

The page is built with DOM APIs only (text nodes, createElement), and the CSP
forbids inline scripts and styles. These checks keep it that way.
"""

from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"


def test_app_js_uses_no_html_string_sinks():
    js = (STATIC / "app.js").read_text()
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write",
                 "eval(", "new Function"):
        assert sink not in js, f"app.js uses {sink}"
    # Attribute-based script or style injection.
    assert not re.search(r"setAttribute\(\s*['\"](style|on\w+)", js)


def test_index_html_has_no_inline_code_or_styles():
    html = (STATIC / "index.html").read_text()
    for tag in re.findall(r"<script\b[^>]*>", html, re.I):
        assert "src=" in tag, f"inline script: {tag}"
    assert "<style" not in html.lower()
    assert not re.search(r"\sstyle\s*=", html, re.I), "inline style attribute"
    assert not re.search(r"\son[a-z]+\s*=", html, re.I), "inline event handler"
