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

from jinja2 import Environment, FileSystemLoader

import re
from markupsafe import escape as html_escape
from explainer import ExplainedIssue


# Jinja2 används för att fylla i HTML-mallen med data.
# Det är samma princip som t.ex. Django-templates.
_template_dir = Path(__file__).parent / "templates"
_jinja_env = Environment(loader=FileSystemLoader(str(_template_dir)))


_MAX_ELEMENTS_SHOWN = 999


def _describe_element(selector: str, html: str) -> str:
    """Generate a short human-readable label from an element's selector and HTML."""
    h = (html or "").lower()
    if "<img" in h:
        return "Bild utan alt-text"
    if "<a" in h:
        href = re.search(r'href=["\']([^"\']+)["\']', html or "")
        if href:
            val = href.group(1)
            if val.startswith("tel:"):
                return f"Telefon-länk: {val[4:]}"
            if val.startswith("mailto:"):
                return f"E-postlänk: {val[7:]}"
            path = val.rstrip("/").split("/")[-1] or val
            return f"Länk: /{path[:50]}" if "/" in val else f"Länk: {val[:50]}"
        return "Länk utan synlig text"
    if "<button" in h:
        text = re.search(r'<button[^>]*>([^<]+)', html or "")
        label = text.group(1).strip()[:40] if text else ""
        return f'Knapp: "{label}"' if label else "Knapp utan synlig text"
    if "<input" in h:
        t = re.search(r'type=["\']([^"\']+)["\']', html or "")
        name = re.search(r'(?:name|id|placeholder)=["\']([^"\']+)["\']', html or "")
        typ = t.group(1) if t else "text"
        label = f": {name.group(1)}" if name else ""
        return f"Formulärfält ({typ}{label})"
    # Fallback: use last segment of the CSS selector
    last = (selector or "").split(">")[-1].strip()
    return last[:70] + ("…" if len(last) > 70 else last) if last else selector[:70]


def _md_to_html(text: str) -> str:
    """Convert AI-returned markdown (inline code, fenced code blocks) to safe HTML."""
    if not text:
        return ""
    safe = str(html_escape(text))
    safe = re.sub(r'```[^\n]*\n(.*?)```', r'<pre><code>\1</code></pre>', safe, flags=re.DOTALL)
    safe = re.sub(r'`([^`\n]+)`', r'<code>\1</code>', safe)
    safe = re.sub(r'\n\n+', '</p><p>', safe)
    safe = safe.replace('\n', '<br>')
    return safe


def _truncate_html(html: str, max_len: int = 200) -> str:
    """Kortar ner HTML-strängar och tar bort base64-data."""
    html = re.sub(r'(data:[^"\'\s]+)', 'data:...', html)
    html = re.sub(r'style="[^"]{80,}"', 'style="..."', html)
    if len(html) > max_len:
        html = html[:max_len] + "…"
    return html


_DIGG_CATEGORIES = {
    "Störande": {
        "icon": "⚡",
        "rules": {"scrolling-region-focusable", "timing-adjustable", "pause-stop-hide", "no-autoplay-audio"},
        "wcag_prefix": ("2.2",),
    },
    "Struktur": {
        "icon": "🗂",
        "rules": {
            "heading-order", "page-has-heading-one", "landmark-one-main", "landmark-unique",
            "bypass", "link-name", "duplicate-id", "frame-title", "list", "listitem",
            "definition-list", "dlitem", "document-title", "html-has-lang", "html-lang-valid",
            "valid-lang", "skip-link",
        },
        "wcag_prefix": ("1.3", "2.4"),
    },
    "Tangentbord": {
        "icon": "⌨️",
        "rules": {
            "accesskeys", "focus-order-semantics", "tabindex", "scrollable-region-focusable",
            "keyboard", "no-keyboard-trap",
        },
        "wcag_prefix": ("2.1",),
    },
    "Storlek & Skärm": {
        "icon": "📐",
        "rules": {"meta-viewport", "target-size", "target-size-minimum"},
        "wcag_prefix": ("1.4.4", "1.4.10", "2.5"),
    },
    "Färg & Form": {
        "icon": "🎨",
        "rules": {"color-contrast", "color-contrast-enhanced"},
        "wcag_prefix": ("1.4.1", "1.4.3", "1.4.6", "1.4.11"),
    },
    "Bilder, Ljud & Video": {
        "icon": "🖼",
        "rules": {
            "image-alt", "image-redundant-alt", "input-image-alt", "role-img-alt",
            "svg-img-alt", "video-caption", "audio-caption", "video-description",
        },
        "wcag_prefix": ("1.1", "1.2"),
    },
    "Formulär": {
        "icon": "📝",
        "rules": {
            "label", "label-content-name-mismatch", "select-name", "autocomplete-valid",
            "form-field-multiple-labels",
        },
        "wcag_prefix": ("1.3.5", "3.3"),
    },
    "Kod": {
        "icon": "💻",
        "rules": {
            "aria-allowed-attr", "aria-allowed-role", "aria-command-name", "aria-deprecated-role",
            "aria-hidden-body", "aria-hidden-focus", "aria-input-field-name",
            "aria-meter-name", "aria-progressbar-name", "aria-required-attr",
            "aria-required-children", "aria-required-parent", "aria-roledescription",
            "aria-roles", "aria-text", "aria-toggle-field-name", "aria-tooltip-name",
            "aria-valid-attr", "aria-valid-attr-value", "button-name", "duplicate-id-active",
            "duplicate-id-aria", "role-img-alt",
        },
        "wcag_prefix": ("4.1",),
    },
}


def _digg_category(rule_id: str, wcag_ref: str) -> str:
    for cat, meta in _DIGG_CATEGORIES.items():
        if rule_id in meta["rules"]:
            return cat
        for prefix in meta["wcag_prefix"]:
            if wcag_ref.startswith(prefix):
                return cat
    return "Kod"


def _severity_label(worst_impact: str) -> str:
    return {
        "critical": "critical",
        "serious": "serious",
        "moderate": "moderate",
        "minor": "minor",
    }.get(worst_impact, "minor")


_IMPACT_ORDER = {"critical": 0, "serious": 1, "moderate": 2, "minor": 3}


def _group_issues(items: List[ExplainedIssue]) -> list:
    """Grupperar problem per rule_id och sorterar dem i DIGG-kategorier."""
    from collections import OrderedDict
    groups: OrderedDict = OrderedDict()
    for item in items:
        key = item.issue.rule_id
        if key not in groups:
            groups[key] = {
                "rule_id": item.issue.rule_id,
                "wcag_reference": item.issue.wcag_reference,
                "impact": item.issue.impact,
                "plain_swedish": _md_to_html(item.plain_swedish),
                "suggested_fix": _md_to_html(item.suggested_fix),
                "confidence": item.confidence,
                "citations": item.citations,
                "help_url": item.issue.help_url,
                "screenshot_b64": item.issue.screenshot_b64,
                "selectors": [],
                "count": 0,
                "digg_category": _digg_category(item.issue.rule_id, item.issue.wcag_reference or ""),
            }
        groups[key]["count"] += 1
        if len(groups[key]["selectors"]) < _MAX_ELEMENTS_SHOWN:
            groups[key]["selectors"].append({
                "label": _describe_element(item.issue.selector, item.issue.affected_html),
                "html": str(html_escape(_truncate_html(item.issue.affected_html))),
            })
    return list(groups.values())


def _group_by_digg(groups: list) -> list:
    """Returnerar lista av (kategori, icon, issues, worst_impact) sorterat per DIGG-kategori."""
    cats: dict = {cat: [] for cat in _DIGG_CATEGORIES}
    cats["Övrigt"] = []
    for g in groups:
        cat = g.get("digg_category", "Övrigt")
        cats.setdefault(cat, []).append(g)
    result = []
    for cat, issues in cats.items():
        if not issues:
            continue
        worst = min(issues, key=lambda x: _IMPACT_ORDER.get(x["impact"], 9))["impact"]
        icon = _DIGG_CATEGORIES.get(cat, {}).get("icon", "🔍")
        result.append({"category": cat, "icon": icon, "issues": issues, "worst_impact": worst})
    return result


def generate_html(url: str, items: List[ExplainedIssue]) -> str:
    """
    Genererar en interaktiv HTML-rapport och returnerar den som sträng.
    """
    count_by_impact = {
        "critical": sum(1 for i in items if i.issue.impact == "critical"),
        "serious":  sum(1 for i in items if i.issue.impact == "serious"),
        "moderate": sum(1 for i in items if i.issue.impact == "moderate"),
        "minor":    sum(1 for i in items if i.issue.impact == "minor"),
    }

    grouped = _group_issues(items)
    digg_groups = _group_by_digg(grouped)

    template = _jinja_env.get_template("report.html")
    return template.render(
        url=url,
        scan_date=datetime.now().strftime("%Y-%m-%d %H:%M"),
        digg_groups=digg_groups,
        total_issues=len(items),
        count_by_impact=count_by_impact,
    )
