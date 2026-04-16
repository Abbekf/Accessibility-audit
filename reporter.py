"""
reporter.py

Sätter ihop allt till en läsbar PDF-rapport.

Vad den gör:
1. Tar en lista med förklarade fel (från explainer.py)
2. Renderar en HTML-mall med Jinja2
3. Konverterar HTML:en till PDF med WeasyPrint
4. Returnerar PDF:en som bytes (som sen kan skickas till användaren)
"""

import os
import sys
from datetime import datetime
from pathlib import Path
from typing import List

# WeasyPrint behöver GTK-bibliotek (gobject, pango, cairo).
# På Windows måste vi lägga till MSYS2:s bin i PATH så att cffi.dlopen hittar dem.
_msys_bin = r"C:\msys64\mingw64\bin"
if sys.platform == "win32" and Path(_msys_bin).exists():
    os.add_dll_directory(_msys_bin)
    if _msys_bin not in os.environ.get("PATH", ""):
        os.environ["PATH"] = _msys_bin + os.pathsep + os.environ["PATH"]

from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML

import re
from explainer import ExplainedIssue


# Jinja2 används för att fylla i HTML-mallen med data.
# Det är samma princip som t.ex. Django-templates.
_template_dir = Path(__file__).parent / "templates"
_jinja_env = Environment(loader=FileSystemLoader(str(_template_dir)))


def _truncate_html(html: str, max_len: int = 200) -> str:
    """Kortar ner HTML-strängar och tar bort base64-data."""
    html = re.sub(r'(data:[^"\'\s]+)', 'data:...', html)
    html = re.sub(r'style="[^"]{80,}"', 'style="..."', html)
    if len(html) > max_len:
        html = html[:max_len] + "…"
    return html


def _group_issues(items: List[ExplainedIssue]) -> list:
    """Grupperar problem med samma rule_id."""
    from collections import OrderedDict
    groups: OrderedDict = OrderedDict()
    for item in items:
        key = item.issue.rule_id
        if key not in groups:
            groups[key] = {
                "rule_id": item.issue.rule_id,
                "wcag_reference": item.issue.wcag_reference,
                "impact": item.issue.impact,
                "plain_swedish": item.plain_swedish,
                "suggested_fix": item.suggested_fix,
                "confidence": item.confidence,
                "citations": item.citations,
                "help_url": item.issue.help_url,
                "selectors": [],
                "count": 0,
            }
        groups[key]["count"] += 1
        groups[key]["selectors"].append({
            "selector": item.issue.selector,
            "html": _truncate_html(item.issue.affected_html),
        })
    return list(groups.values())


def generate_pdf(url: str, items: List[ExplainedIssue]) -> bytes:
    """
    Genererar en PDF-rapport och returnerar den som bytes.
    """
    count_by_impact = {
        "critical": sum(1 for i in items if i.issue.impact == "critical"),
        "serious":  sum(1 for i in items if i.issue.impact == "serious"),
        "moderate": sum(1 for i in items if i.issue.impact == "moderate"),
        "minor":    sum(1 for i in items if i.issue.impact == "minor"),
    }

    grouped = _group_issues(items)

    template = _jinja_env.get_template("report.html")
    html_content = template.render(
        url=url,
        scan_date=datetime.now().strftime("%Y-%m-%d %H:%M"),
        groups=grouped,
        total_issues=len(items),
        count_by_impact=count_by_impact,
    )

    pdf_bytes = HTML(string=html_content).write_pdf()
    return pdf_bytes
