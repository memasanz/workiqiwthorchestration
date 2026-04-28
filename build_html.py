"""Render PLAN.md to PLAN.html with styled HTML and preserved ASCII diagrams."""
from pathlib import Path
import markdown

ROOT = Path(__file__).parent
md_text = (ROOT / "PLAN.md").read_text(encoding="utf-8")

html_body = markdown.markdown(
    md_text,
    extensions=["fenced_code", "tables", "toc", "codehilite"],
    extension_configs={"codehilite": {"guess_lang": False, "css_class": "codehilite"}},
)

template = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Multi-Person Workflow — Implementation Plan</title>
<style>
  :root {{
    --fg: #1f2328;
    --muted: #57606a;
    --bg: #ffffff;
    --code-bg: #f6f8fa;
    --border: #d0d7de;
    --accent: #0969da;
  }}
  html, body {{ background: var(--bg); color: var(--fg); }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Helvetica Neue",
                 Arial, sans-serif;
    line-height: 1.55;
    max-width: 980px;
    margin: 2rem auto;
    padding: 0 1.5rem 4rem;
  }}
  h1, h2, h3, h4 {{ line-height: 1.25; margin-top: 2rem; }}
  h1 {{ border-bottom: 1px solid var(--border); padding-bottom: .3em; }}
  h2 {{ border-bottom: 1px solid var(--border); padding-bottom: .3em; }}
  a {{ color: var(--accent); }}
  code {{
    font-family: "SF Mono", "Cascadia Code", Consolas, "Liberation Mono",
                 monospace;
    font-size: 0.92em;
    background: var(--code-bg);
    padding: 0.15em 0.35em;
    border-radius: 4px;
  }}
  pre {{
    background: var(--code-bg);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 1rem;
    overflow-x: auto;
    font-family: "SF Mono", "Cascadia Code", Consolas, "Liberation Mono",
                 monospace;
    font-size: 0.82em;
    line-height: 1.4;
  }}
  pre code {{ background: transparent; padding: 0; font-size: inherit; }}
  table {{
    border-collapse: collapse;
    margin: 1rem 0;
    width: 100%;
    display: block;
    overflow-x: auto;
  }}
  th, td {{
    border: 1px solid var(--border);
    padding: 0.5rem 0.75rem;
    text-align: left;
    vertical-align: top;
  }}
  th {{ background: var(--code-bg); }}
  blockquote {{
    border-left: 4px solid var(--border);
    color: var(--muted);
    margin: 1rem 0;
    padding: 0 1rem;
  }}
  .meta {{ color: var(--muted); font-size: 0.9em; margin-bottom: 2rem; }}
</style>
</head>
<body>
<div class="meta">Multi-Person Workflow — Implementation Plan · Generated from PLAN.md</div>
{body}
</body>
</html>
"""

(ROOT / "PLAN.html").write_text(template.format(body=html_body), encoding="utf-8")
print(f"Wrote {ROOT / 'PLAN.html'}")
