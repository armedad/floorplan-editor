"""The harness instructions in AGENTS.md, shared by the MCP server and the About page."""

import html
import re
from pathlib import Path

GITHUB_INSTRUCTIONS = "https://github.com/armedad/floorplan-editor/blob/main/AGENTS.md"


def instructions_path() -> Path | None:
    here = Path(__file__).resolve()
    for candidate in (here.parents[1] / "AGENTS.md", here.parent / "AGENTS.md"):
        if candidate.is_file():
            return candidate
    return None


def instructions_text() -> str:
    path = instructions_path()
    return path.read_text(encoding="utf-8") if path else ""


def _inline(text: str) -> str:
    parts = re.split(r"(`[^`]+`)", text)
    out = []
    for part in parts:
        if part.startswith("`") and part.endswith("`") and len(part) > 2:
            out.append(f"<code>{html.escape(part[1:-1])}</code>")
            continue
        escaped = html.escape(part)
        escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
        escaped = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r'<a href="\2">\1</a>', escaped)
        out.append(escaped)
    return "".join(out)


def _markdown_html(source: str) -> str:
    lines = source.splitlines()
    blocks: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            buf = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                buf.append(html.escape(lines[i]))
                i += 1
            blocks.append("<pre><code>" + "\n".join(buf) + "</code></pre>")
            i += 1
            continue
        if line.startswith("### "):
            blocks.append(f"<h3>{_inline(line[4:])}</h3>")
        elif line.startswith("## "):
            blocks.append(f"<h2>{_inline(line[3:])}</h2>")
        elif line.startswith("# "):
            blocks.append(f"<h1>{_inline(line[2:])}</h1>")
        elif line.startswith("- "):
            items = []
            while i < len(lines) and lines[i].startswith("- "):
                items.append(f"<li>{_inline(lines[i][2:])}</li>")
                i += 1
            blocks.append("<ul>" + "".join(items) + "</ul>")
            continue
        elif re.match(r"\d+\. ", line):
            items = []
            while i < len(lines) and re.match(r"\d+\. ", lines[i]):
                items.append(f"<li>{_inline(re.sub(r'^\d+\. ', '', lines[i]))}</li>")
                i += 1
            blocks.append("<ol>" + "".join(items) + "</ol>")
            continue
        elif line.strip():
            buf = [line]
            i += 1
            while i < len(lines) and lines[i].strip() and not lines[i].startswith(("#", "- ", "```")) and not re.match(r"\d+\. ", lines[i]):
                buf.append(lines[i])
                i += 1
            blocks.append(f"<p>{_inline(' '.join(buf))}</p>")
            continue
        i += 1
    return "\n".join(blocks)


def instructions_html() -> str:
    source = instructions_text()
    if not source:
        return ""
    body = _markdown_html(source)
    github = html.escape(GITHUB_INSTRUCTIONS)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Using Floor Plan-It with an AI harness</title>
  <style>
    body {{ font: 16px/1.5 "Segoe UI", sans-serif; color: #1d2329; max-width: 42rem; margin: 2rem auto; padding: 0 1.25rem 3rem; }}
    a {{ color: #1565c0; }}
    pre {{ background: #f4f6f8; padding: 12px 14px; overflow: auto; border-radius: 6px; }}
    code {{ font-family: Consolas, monospace; font-size: 0.92em; }}
    h1 {{ font-size: 1.6rem; }}
  </style>
</head>
<body>
{body}
<p><a href="{github}">These instructions on GitHub</a></p>
</body>
</html>"""
