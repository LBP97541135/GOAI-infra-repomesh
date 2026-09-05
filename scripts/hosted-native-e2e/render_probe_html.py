#!/usr/bin/env python3
"""Render a probe transcript (output/hosted-native-e2e/<date>/NN.txt)
as a dark terminal-style HTML page
so it can be screenshotted at 1440x900 like the console pages.

Usage: render_probe_html.py <txt> <html> [title]
The page shows the full command line and the complete output; the screenshot is of this rendering,
which is stated in the report (there is no physical terminal window in this session).
"""

import html
import sys
from pathlib import Path

TEMPLATE = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8"><title>{title}</title>
<style>
  html, body {{ margin: 0; background: #0b0f0c; color: #d7dbd8; }}
  body {{ font: 14px/1.45 "Cascadia Mono", "JetBrains Mono", Consolas, "Courier New", monospace; }}
  .bar {{ background: #17211b; color: #cfae62; padding: 10px 18px; font-size: 13px;
         display: flex; justify-content: space-between; }}
  .bar span {{ color: #8fa398; }}
  pre {{ margin: 0; padding: 16px 18px; white-space: pre-wrap; word-break: break-all; }}
  .cmd {{ color: #e8d7a8; }}
  .meta {{ color: #6f8577; }}
  .ok {{ color: #7fc48b; }}
  .bad {{ color: #e07a6a; }}
</style></head>
<body>
<div class="bar"><div>{title}</div><span>{source}</span></div>
<pre>{body}</pre>
</body></html>
"""


def classify(line: str) -> str:
    esc = html.escape(line)
    if line.startswith("$ "):
        return f'<span class="cmd">{esc}</span>'
    if line.startswith("# exit: 0"):
        return f'<span class="ok">{esc}</span>'
    if line.startswith("# exit:"):
        return f'<span class="bad">{esc}</span>'
    if line.startswith("# "):
        return f'<span class="meta">{esc}</span>'
    return esc


def main() -> None:
    src = Path(sys.argv[1])
    dst = Path(sys.argv[2])
    title = sys.argv[3] if len(sys.argv) > 3 else src.stem
    lines = src.read_text(encoding="utf-8", errors="replace").splitlines()
    body = "\n".join(classify(line) for line in lines)
    dst.write_text(
        TEMPLATE.format(title=html.escape(title), source=html.escape(src.name), body=body),
        encoding="utf-8",
    )
    print(dst)


if __name__ == "__main__":
    main()
