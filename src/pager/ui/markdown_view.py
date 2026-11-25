"""Markdown → HTML für QTextBrowser/PDF (Qt unterstützt nur eine CSS-Teilmenge)."""

from __future__ import annotations

import markdown

from pager.ui.theme import C

CSS = f"""
body {{ color: {C['text']}; }}
h1 {{ font-size: 19pt; font-weight: 700; margin: 4px 0 10px 0; }}
h2 {{ font-size: 14pt; font-weight: 650; color: #22355E; margin: 22px 0 8px 0; }}
h3 {{ font-size: 11.5pt; font-weight: 650; margin: 16px 0 6px 0; }}
p, li {{ line-height: 145%; }}
table {{ border-collapse: collapse; margin: 6px 0 12px 0; }}
th {{ background: {C['surface_alt']}; color: {C['muted']}; font-weight: 600; text-align: left;
      border-bottom: 1px solid {C['border_strong']}; }}
td {{ border-bottom: 1px solid {C['border']}; }}
blockquote {{ background: {C['warning_soft']}; margin: 0 0 12px 0; padding: 8px 12px; }}
code {{ font-family: Consolas, Menlo, monospace; background: {C['surface_alt']}; }}
pre {{ font-family: Consolas, Menlo, monospace; background: {C['surface_alt']}; padding: 10px; }}
a {{ color: {C['accent']}; }}
"""

_md = markdown.Markdown(extensions=["tables", "fenced_code", "sane_lists"])


def to_html(text: str) -> str:
    _md.reset()
    html = _md.convert(text)
    # Qt-Tabellen brauchen Attribute statt CSS für Breite/Abstände
    html = html.replace("<table>", '<table width="100%" cellspacing="0" cellpadding="7">')
    # Ausrichtung als Attribut, damit das CSS (Rahmen) der Zelle erhalten bleibt
    for side in ("left", "right", "center"):
        html = html.replace(f' style="text-align: {side};"', f' align="{side}"')
    return html
