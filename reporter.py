

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

_IMAGE_RULES_SET = {
    "image-alt", "input-image-alt", "role-img-alt",
    "svg-img-alt", "image-redundant-alt",
}


_RULE_PROBLEM = {
    "image-alt":                  "saknar alt-text",
    "input-image-alt":            "saknar alt-text",
    "role-img-alt":               "saknar alt-text",
    "svg-img-alt":                "saknar alt-text",
    "image-redundant-alt":        "har redundant alt-text",
    "label":                      "saknar etikett",
    "label-content-name-mismatch":"etikett stämmer inte med synlig text",
    "select-name":                "saknar etikett",
    "button-name":                "saknar synlig text",
    "link-name":                  "saknar synlig text",
    "color-contrast":             "har otillräcklig kontrast",
    "color-contrast-enhanced":    "har otillräcklig kontrast (förhöjd nivå)",
    "heading-order":              "bryter rubrikordningen",
    "page-has-heading-one":       "sidan saknar H1-rubrik",
    "duplicate-id":               "har duplicerat ID",
    "duplicate-id-active":        "har duplicerat aktivt ID",
    "duplicate-id-aria":          "har duplicerat ARIA-ID",
    "aria-required-attr":         "saknar obligatoriskt ARIA-attribut",
    "aria-required-children":     "saknar obligatoriska barn-element",
    "aria-required-parent":       "saknar obligatoriskt förälder-element",
    "aria-roles":                 "har ogiltigt ARIA-roll",
    "aria-valid-attr":            "har ogiltigt ARIA-attribut",
    "aria-valid-attr-value":      "har ogiltigt ARIA-attributvärde",
    "aria-hidden-focus":          "är dolt för skärmläsare men kan fokuseras",
    "aria-hidden-body":           "hela sidan är dold för skärmläsare",
    "scrollable-region-focusable":"rullningsbart område kan inte nås med tangentbord",
    "scrolling-region-focusable": "rullningsbart område kan inte nås med tangentbord",
    "keyboard":                   "kan inte nås med tangentbord",
    "focus-order-semantics":      "har fel fokusordning",
    "tabindex":                   "har felaktigt tabindex",
    "bypass":                     "blockerar hopp till huvudinnehåll",
    "document-title":             "sidan saknar titel",
    "html-has-lang":              "saknar språkattribut",
    "html-lang-valid":            "har ogiltigt språkattribut",
    "frame-title":                "saknar titel",
    "meta-viewport":              "blockerar zoom",
    "target-size":                "är för liten att trycka på",
    "autocomplete-valid":         "har felaktigt autocomplete-värde",
    "nested-interactive":         "innehåller ett annat klickbart element (nästlad interaktivitet)",
    "landmark-one-main":          "sidan saknar main-landmärke",
    "landmark-unique":            "landmärke är inte unikt",
    "list":                       "har felaktig liststruktur",
    "listitem":                   "listelement används utanför lista",
    "definition-list":            "har felaktig definitionslistestruktur",
    "dlitem":                     "definitionselement används utanför lista",
    "video-caption":              "saknar textning",
    "audio-caption":              "saknar textning",
}

_TAG_LABELS = {
    "summary":    "Expanderbart avsnitt",
    "details":    "Expanderbart avsnitt",
    "nav":        "Navigeringsmeny",
    "header":     "Sidhuvud",
    "footer":     "Sidfot",
    "main":       "Huvudinnehåll",
    "aside":      "Sidopanel",
    "section":    "Sektion",
    "article":    "Artikel",
    "form":       "Formulär",
    "select":     "Vallistor",
    "textarea":   "Textfält",
    "table":      "Tabell",
    "iframe":     "Inbäddad sida (iframe)",
    "video":      "Video",
    "audio":      "Ljud",
    "svg":        "SVG-grafik",
    "canvas":     "Canvas-element",
    "dialog":     "Dialog/modal",
    "h1": "Rubrik (H1)", "h2": "Rubrik (H2)", "h3": "Rubrik (H3)",
    "h4": "Rubrik (H4)", "h5": "Rubrik (H5)", "h6": "Rubrik (H6)",
}


def _extract_inner_text(html: str, max_len: int = 50) -> str:
    """Plockar ut synlig text ur en HTML-snutt."""
    text = re.sub(r'<[^>]+>', ' ', html or "")
    text = re.sub(r'\s+', ' ', text).strip()
    if len(text) > max_len:
        return text[:max_len] + "…"
    return text


def _describe_element(selector: str, html: str, rule_id: str = "", index: int = 0) -> str:
    """Genererar en läsbar svensk etikett som beskriver elementet och varför det är markerat."""
    problem = _RULE_PROBLEM.get(rule_id, "")
    num = f" #{index}" if index > 1 else ""
    h = (html or "").lower()

    # ── Bilder ──
    if "<img" in h:
        src = re.search(r'src=["\']([^"\']+)["\']', html or "")
        if src:
            val = src.group(1)
            if not val.startswith("data:"):
                filename = val.rstrip("/").split("/")[-1].split("?")[0]
                return f"Bild{num}: {filename[:50]} — {problem}" if filename else f"Bild{num} — {problem}"
        alt = re.search(r'alt=["\']([^"\']*)["\']', html or "")
        if alt and not alt.group(1).strip():
            return f"Bild{num} — tomt alt-attribut (dekorativ?)"
        return f"Bild{num} — {problem or 'saknar alt-text'}"

    # ── Länkar ──
    if re.search(r'<a[\s>]', h):
        inner = _extract_inner_text(html)
        href = re.search(r'href=["\']([^"\']+)["\']', html or "")
        if href:
            val = href.group(1)
            if val.startswith("tel:"):
                return f"Telefon-länk: {val[4:]} — {problem or 'saknar synlig text'}"
            if val.startswith("mailto:"):
                return f"E-postlänk: {val[7:]} — {problem or 'saknar synlig text'}"
        if inner:
            return f"Länk{num}: \"{inner}\" — {problem}" if problem else f"Länk{num}: \"{inner}\""
        return f"Länk{num} — {problem or 'saknar synlig text'}"

    # ── Knappar ──
    if "<button" in h:
        inner = _extract_inner_text(html)
        aria = re.search(r'aria-label=["\']([^"\']+)["\']', html or "")
        name = aria.group(1) if aria else inner
        if name:
            return f"Knapp{num}: \"{name[:40]}\" — {problem}" if problem else f"Knapp{num}: \"{name[:40]}\""
        return f"Knapp{num} — {problem or 'saknar synlig text'}"

    # ── Formulärfält ──
    if "<input" in h:
        t    = re.search(r'type=["\']([^"\']+)["\']', html or "")
        name = re.search(r'(?:placeholder|aria-label|name|id)=["\']([^"\']+)["\']', html or "")
        typ  = t.group(1) if t else "text"
        lbl  = f" \"{name.group(1)[:30]}\"" if name else ""
        return f"Formulärfält ({typ}){lbl}{num} — {problem or 'saknar etikett'}"

    if "<select" in h:
        name = re.search(r'(?:name|id|aria-label)=["\']([^"\']+)["\']', html or "")
        lbl = f" \"{name.group(1)[:30]}\"" if name else ""
        return f"Valruta{lbl}{num} — {problem or 'saknar etikett'}"

    if "<textarea" in h:
        name = re.search(r'(?:name|id|placeholder)=["\']([^"\']+)["\']', html or "")
        lbl = f" \"{name.group(1)[:30]}\"" if name else ""
        return f"Textfält{lbl}{num} — {problem or 'saknar etikett'}"

    # ── Rubriknivåer ──
    for level in range(1, 7):
        if f"<h{level}" in h:
            inner = _extract_inner_text(html)
            base = f"Rubrik H{level}: \"{inner}\"" if inner else f"Rubrik H{level}{num}"
            return f"{base} — {problem}" if problem else base

    # ── Övriga kända taggar ──
    tag_match = re.search(r'<([a-z][a-z0-9]*)', h)
    if tag_match:
        tag = tag_match.group(1)
        tag_label = _TAG_LABELS.get(tag, "")
        inner = _extract_inner_text(html)
        if tag_label:
            base = f"{tag_label}{num}: \"{inner}\"" if inner else f"{tag_label}{num}"
            return f"{base} — {problem}" if problem else base
        if inner:
            base = f"<{tag}>{num}: \"{inner}\""
            return f"{base} — {problem}" if problem else base

    # ── Sista utväg ──
    last = (selector or "").split(">")[-1].strip().split(":nth")[0].strip()
    base = last[:55] if last else (selector or "")[:55]
    return f"{base}{num} — {problem}" if problem else base


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
    """
    Grupperar problem per rule_id — utom bildregler som får ett eget kort per element
    så att AI:ns dekorativ/innehållsbärande-bedömning kan ge individuell allvarlighetsgrad.
    """
    from collections import OrderedDict
    groups: OrderedDict = OrderedDict()
    image_counters: dict = {}  # rule_id → antal för unik nyckel

    for item in items:
        rule_id = item.issue.rule_id
        is_image_rule = rule_id in _IMAGE_RULES_SET

        if is_image_rule:
            image_counters[rule_id] = image_counters.get(rule_id, 0) + 1
            key = f"{rule_id}__{image_counters[rule_id]}"
            effective_impact = item.adjusted_impact or item.issue.impact
            label = _describe_element(item.issue.selector, item.issue.affected_html, rule_id, image_counters[rule_id])
            groups[key] = {
                "rule_id": rule_id,
                "wcag_reference": item.issue.wcag_reference,
                "impact": effective_impact,
                "plain_swedish": _md_to_html(item.plain_swedish),
                "suggested_fix": _md_to_html(item.suggested_fix),
                "confidence": item.confidence,
                "citations": item.citations,
                "help_url": item.issue.help_url,
                "screenshot_b64": item.issue.screenshot_b64,
                "selectors": [{
                    "label": label,
                    "html": str(html_escape(_truncate_html(item.issue.affected_html))),
                    "selector": item.issue.selector,
                    "source_url": item.issue.source_url or "",
                    "vision_fix": _md_to_html(item.suggested_fix),
                }],
                "count": 1,
                "digg_category": _digg_category(rule_id, item.issue.wcag_reference or ""),
                "is_image_rule": True,
            }
        else:
            key = rule_id
            if key not in groups:
                groups[key] = {
                    "rule_id": rule_id,
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
                    "digg_category": _digg_category(rule_id, item.issue.wcag_reference or ""),
                    "is_image_rule": False,
                }
            groups[key]["count"] += 1
            if len(groups[key]["selectors"]) < _MAX_ELEMENTS_SHOWN:
                el_index = groups[key]["count"]
                groups[key]["selectors"].append({
                    "label": _describe_element(item.issue.selector, item.issue.affected_html, rule_id, el_index),
                    "html": str(html_escape(_truncate_html(item.issue.affected_html))),
                    "selector": item.issue.selector,
                    "source_url": item.issue.source_url or "",
                    "vision_fix": "",
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


def generate_presentation_html(url: str, items: List[ExplainedIssue]) -> str:
    """Genererar en slide-presentation i HTML-format."""
    grouped = _group_issues(items)
    count_by_impact = {
        "critical": sum(1 for g in grouped if g["impact"] == "critical"),
        "serious":  sum(1 for g in grouped if g["impact"] == "serious"),
        "moderate": sum(1 for g in grouped if g["impact"] == "moderate"),
        "minor":    sum(1 for g in grouped if g["impact"] == "minor"),
    }
    digg_groups = _group_by_digg(grouped)

    from urllib.parse import urlparse
    site_name = urlparse(url).netloc.replace("www.", "").split(".")[0].capitalize()

    template = _jinja_env.get_template("presentation.html")
    return template.render(
        url=url,
        site_name=site_name,
        scan_date=datetime.now().strftime("%Y-%m-%d %H:%M"),
        digg_groups=digg_groups,
        total_issues=len(grouped),
        unique_rules=len(grouped),
        num_categories=len(digg_groups),
        count_by_impact=count_by_impact,
    )


def generate_html(url: str, items: List[ExplainedIssue], site_logo_b64: str = "") -> str:
    """
    Genererar en interaktiv HTML-rapport och returnerar den som sträng.
    """
    grouped = _group_issues(items)
    count_by_impact = {
        "critical": sum(1 for g in grouped if g["impact"] == "critical"),
        "serious":  sum(1 for g in grouped if g["impact"] == "serious"),
        "moderate": sum(1 for g in grouped if g["impact"] == "moderate"),
        "minor":    sum(1 for g in grouped if g["impact"] == "minor"),
    }
    digg_groups = _group_by_digg(grouped)

    from urllib.parse import urlparse
    site_name = urlparse(url).netloc.replace("www.", "").split(".")[0].capitalize()

    template = _jinja_env.get_template("report.html")
    return template.render(
        url=url,
        site_name=site_name,
        site_logo_b64=site_logo_b64,
        scan_date=datetime.now().strftime("%Y-%m-%d %H:%M"),
        digg_groups=digg_groups,
        total_issues=len(grouped),
        count_by_impact=count_by_impact,
    )
