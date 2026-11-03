#!/usr/bin/env python3
"""
build_handoff_pdfs.py — Build the two shareable PDFs for the v5 tunnel release.

    handoff/Tunneling-v5-Code.pdf      full v5 source + diffs of every touched file
    handoff/Gemini-4-Argon-Tasks.pdf  prompt-ready task pack for Gemini 4 Argon

PDF generation is a *documentation artifact* step, so it is the only place in this
repository allowed to use third-party libraries. Install them once:

    python3 -m pip install --break-system-packages reportlab pygments

Then, from the repository root:

    python3 roblox-bridge/tools/build_handoff_pdfs.py
    python3 roblox-bridge/tools/build_handoff_pdfs.py --code-only
    python3 roblox-bridge/tools/build_handoff_pdfs.py --base 92cde4e   # diff base

Nothing here runs at bridge runtime; `server.py` and the MCP server never import it.
"""

import argparse
import datetime
import json
import re
import subprocess
import sys
from pathlib import Path

try:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.platypus import (
        BaseDocTemplate,
        Flowable,
        Frame,
        KeepTogether,
        NextPageTemplate,
        PageBreak,
        PageTemplate,
        Paragraph,
        Preformatted,
        Spacer,
        Table,
        TableStyle,
        XPreformatted,
    )
    from pygments import lex
    from pygments.lexers import DiffLexer, JsonLexer, MarkdownLexer, PythonLexer
    from pygments.token import Comment, Generic, Keyword, Name, Number, Operator, String
except ImportError as exc:  # pragma: no cover - documentation tool only
    sys.stderr.write(
        f"missing PDF dependency: {exc}\n"
        "install with: python3 -m pip install --break-system-packages reportlab pygments\n"
    )
    raise SystemExit(1)


HERE = Path(__file__).resolve().parent
BRIDGE = HERE.parent
ROOT = BRIDGE.parent
OUT_DIR = ROOT / "handoff"

BRANCH = "arena/706ae6d5-tunneling"
VERSION = "5.2.0"
TODAY = datetime.date.today().isoformat()

# GitHub-flavoured palette
INK = colors.HexColor("#1f2328")
MUTED = colors.HexColor("#57606a")
FAINT = colors.HexColor("#8b949e")
RULE = colors.HexColor("#d0d7de")
PANEL = colors.HexColor("#f6f8fa")
PANEL_ALT = colors.HexColor("#eef2f6")
ACCENT = colors.HexColor("#0969da")
GREEN = colors.HexColor("#1a7f37")
RED = colors.HexColor("#cf222e")
PURPLE = colors.HexColor("#8250df")
ORANGE = colors.HexColor("#953800")
BLUE_DARK = colors.HexColor("#0a3069")

TOKEN_COLORS = {
    Comment: "#6e7781",
    Keyword: "#cf222e",
    Keyword.Type: "#953800",
    Name.Builtin: "#953800",
    Name.Function: "#8250df",
    Name.Class: "#8250df",
    Name.Decorator: "#8250df",
    Name.Tag: "#116329",
    Name.Attribute: "#0550ae",
    Name.Variable: "#0550ae",
    String: "#0a3069",
    Number: "#0550ae",
    Operator: "#cf222e",
    Operator.Word: "#cf222e",
    Generic.Deleted: "#82071e",
    Generic.Inserted: "#116329",
    Generic.Heading: "#0550ae",
    Generic.Subheading: "#0550ae",
}


def _color_for(ttype) -> str:
    probe = ttype
    while probe is not None:
        if probe in TOKEN_COLORS:
            return TOKEN_COLORS[probe]
        probe = probe.parent
    return "#1f2328"


SAFE_MAP = {
    "\u2014": "-", "\u2013": "-", "\u2018": "'", "\u2019": "'",
    "\u201c": '"', "\u201d": '"', "\u2026": "...", "\u2192": "->",
    "\u2190": "<-", "\u2191": "^", "\u2193": "v", "\u21b3": ">",
    "\u2514": "+", "\u251c": "+", "\u2502": "|", "\u2500": "-",
    "\u2501": "-", "\u00a0": " ", "\u2022": "*", "\u2713": "OK",
    "\u2717": "x", "\u25b8": ">", "\u2261": "=~",
}


def safe_text(text: str) -> str:
    out = []
    for ch in text:
        ch = SAFE_MAP.get(ch, ch)
        try:
            ch.encode("cp1252")
        except UnicodeEncodeError:
            ch = "?"
        out.append(ch)
    return "".join(out)


def escape_xml(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def git(args, cwd=ROOT) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=str(cwd), check=True,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        ).stdout
    except (subprocess.CalledProcessError, OSError):
        return ""


# ---------------------------------------------------------------------------
# Code rendering
# ---------------------------------------------------------------------------

def _runs(source: str, lexer):
    """Yield (color, text) runs from Pygments, sanitized for ReportLab's fonts."""
    for ttype, value in lex(source, lexer):
        if not value:
            continue
        text = safe_text(value).replace("\t", "    ")
        yield _color_for(ttype), text


def md(text: str) -> str:
    """Escape XML, then render `backticked` spans in monospace for body copy."""
    escaped = escape_xml(safe_text(text))
    for tag in ("b", "i", "u"):
        escaped = escaped.replace(f"&lt;{tag}&gt;", f"<{tag}>").replace(f"&lt;/{tag}&gt;", f"</{tag}>")
    return re.sub(
        r"`([^`]+)`",
        r"<font name='Courier' size='8.1' color='#0a3069'>\1</font>",
        escaped,
    )


def wrap_plain(text: str, width: int = 100) -> str:
    """Word-wrap plain text so XPreformatted never runs past the frame."""
    out = []
    for raw in text.split("\n"):
        expanded = raw.replace("\t", "    ")
        if not expanded.strip():
            out.append("")
            continue
        indent = " " * (len(expanded) - len(expanded.lstrip(" ")))
        body = " ".join(expanded.strip().split())
        current = indent
        for word in body.split(" "):
            while len(word) > max(width - len(indent), 8):
                room = width - len(current)
                if room > 0 and current.strip():
                    out.append(current.rstrip())
                    current = indent
                    continue
                piece = word[: max(width - len(indent), 8)]
                out.append(indent + piece)
                word = word[len(piece):]
            candidate = current + (" " if current.strip() else "") + word
            if len(candidate) > width:
                out.append(current.rstrip())
                current = indent + word
            else:
                current = candidate
        out.append(current.rstrip())
    return "\n".join(out)


def code_lines(source: str, lexer=None, max_chars: int = 196, numbered: bool = True,
               with_kinds: bool = False):
    """Return display lines of highlighted code, hard-wrapped at max_chars.

    Wrapping happens per character while keeping each run in its own font span,
    so long lines never overflow the page and colours survive the break.
    """
    display = [[]]          # list of lines; each line is a list of (color, char)
    numbers = [1]
    width = 0
    lineno = 1
    for color, text in _runs(source, lexer or PythonLexer()):
        for ch in text:
            if ch == "\n":
                display.append([])
                lineno += 1
                numbers.append(lineno if numbered else None)
                width = 0
                continue
            if width >= max_chars:
                display.append([])
                numbers.append(None)
                width = 0
            display[-1].append((color, ch))
            width += 1

    lines = []
    kinds = []
    for chars, number in zip(display, numbers):
        # Merge adjacent characters of the same colour into one span.
        spans = []
        buffer = []
        buffer_color = None
        for color, ch in chars:
            if color != buffer_color:
                if buffer:
                    spans.append((buffer_color, "".join(buffer)))
                buffer = []
                buffer_color = color
            buffer.append(ch)
        if buffer:
            spans.append((buffer_color, "".join(buffer)))
        markup = "".join(
            f'<font color="{color}">{escape_xml(chunk)}</font>' if chunk else ""
            for color, chunk in spans
        )
        if numbered:
            prefix = f'<font color="#8b949e">{number:>4} </font>' if number is not None else " " * 5
            markup = prefix + markup
        lines.append(markup)
        kinds.append(chars[0][1] if chars and chars[0][1] in "+-" else " ")
    if with_kinds:
        return lines, kinds
    return lines


# ---------------------------------------------------------------------------
# Shared document chrome
# ---------------------------------------------------------------------------

def make_styles():
    ss = getSampleStyleSheet()
    styles = {}
    styles["title"] = ParagraphStyle(
        "TitleX", parent=ss["Title"], fontName="Helvetica-Bold", fontSize=30,
        leading=34, textColor=INK, alignment=TA_LEFT, spaceAfter=6,
    )
    styles["subtitle"] = ParagraphStyle(
        "SubTitle", parent=ss["Normal"], fontName="Helvetica", fontSize=13,
        leading=18, textColor=MUTED, spaceAfter=16,
    )
    styles["h1"] = ParagraphStyle(
        "H1X", parent=ss["Heading1"], fontName="Helvetica-Bold", fontSize=17,
        leading=21, textColor=INK, spaceBefore=10, spaceAfter=8,
    )
    styles["h2"] = ParagraphStyle(
        "H2X", parent=ss["Heading2"], fontName="Helvetica-Bold", fontSize=12.5,
        leading=15.5, textColor=ACCENT, spaceBefore=10, spaceAfter=4,
    )
    styles["h3"] = ParagraphStyle(
        "H3X", parent=ss["Heading3"], fontName="Helvetica-Bold", fontSize=10.5,
        leading=14, textColor=INK, spaceBefore=8, spaceAfter=3,
    )
    styles["body"] = ParagraphStyle(
        "BodyX", parent=ss["BodyText"], fontName="Helvetica", fontSize=9.5,
        leading=13.0, textColor=INK, spaceAfter=5,
    )
    styles["small"] = ParagraphStyle(
        "SmallX", parent=styles["body"], fontSize=8.2, leading=11.4, textColor=MUTED,
    )
    styles["mono"] = ParagraphStyle(
        "MonoX", parent=styles["body"], fontName="Courier", fontSize=8.2, leading=11,
    )
    styles["code"] = ParagraphStyle(
        "Code", fontName="Courier", fontSize=6.35, leading=7.55,
        textColor=INK, backColor=PANEL, borderPadding=0,
        leftIndent=7, rightIndent=7, spaceBefore=2, spaceAfter=9,
    )
    styles["code_small"] = ParagraphStyle(
        "CodeSmall", parent=styles["code"], fontSize=6.9, leading=8.8,
    )
    styles["bullet"] = ParagraphStyle(
        "BulletX", parent=styles["body"], leftIndent=12, bulletIndent=2, spaceAfter=3,
    )
    for name, tint in (("diff_line", PANEL), ("diff_add", "#e6ffec"), ("diff_del", "#ffebe9")):
        styles[name] = ParagraphStyle(
            name, fontName="Courier", fontSize=6.35, leading=7.55,
            textColor=INK, backColor=colors.HexColor(tint) if isinstance(tint, str) else tint,
            leftIndent=6, spaceBefore=0, spaceAfter=0, borderPadding=0,
        )
    styles["prompt"] = ParagraphStyle(
        "PromptX", fontName="Courier", fontSize=7.9, leading=10.2,
        textColor=INK, backColor=colors.HexColor("#fbfcfd"),
    )
    return styles


def cover_canvas(canvas, doc, title, subtitle, badge, meta_rows, inside=""):
    width, height = doc.pagesize
    canvas.saveState()
    canvas.setFillColor(colors.HexColor("#0d1117"))
    canvas.rect(0, height - 250, width, 250, stroke=0, fill=1)
    canvas.setFillColor(colors.HexColor("#58a6ff"))
    canvas.rect(0, height - 256, width, 6, stroke=0, fill=1)

    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 27)
    canvas.drawString(38, height - 108, title)
    canvas.setFont("Helvetica", 12.5)
    canvas.setFillColor(colors.HexColor("#9db2c8"))
    canvas.drawString(38, height - 138, subtitle)

    canvas.setFillColor(colors.HexColor("#1f6feb"))
    canvas.roundRect(38, height - 196, stringWidth(badge, "Helvetica-Bold", 10) + 26, 24, 6, stroke=0, fill=1)
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 10)
    canvas.drawString(51, height - 188, badge)

    y = height - 300
    canvas.setFillColor(INK)
    for label, value in meta_rows:
        canvas.setFont("Helvetica-Bold", 9)
        canvas.setFillColor(MUTED)
        canvas.drawString(38, y, label.upper())
        canvas.setFont("Helvetica", 10)
        canvas.setFillColor(INK)
        canvas.drawString(150, y, value)
        y -= 20
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.6)
    canvas.line(38, y - 4, width - 38, y - 4)

    if inside:
        start_y = 268 if height > 700 else 88
        canvas.setFillColor(MUTED)
        canvas.setFont("Helvetica-Bold", 7.6)
        canvas.drawString(38, start_y + 16, "INSIDE")
        canvas.setFont("Helvetica", 8.6)
        words = inside.split()
        line = ""
        lines = []
        for word in words:
            candidate = (line + " " + word).strip()
            if stringWidth(candidate, "Helvetica", 8.6) > width - 90:
                lines.append(line)
                line = word
            else:
                line = candidate
        if line:
            lines.append(line)
        cursor = start_y
        for text_line in lines:
            canvas.drawString(38, cursor, text_line)
            cursor -= 12
    canvas.restoreState()


CURRENT_FILE = ""


class SetFile(Flowable):
    """Zero-height marker that records which file the following pages belong to."""

    def __init__(self, name):
        Flowable.__init__(self)
        self.name = name
        self.width = 0
        self.height = 0

    def wrap(self, availWidth, availHeight):
        return (0, 0)

    def draw(self):
        global CURRENT_FILE
        CURRENT_FILE = self.name


def page_chrome(canvas, doc, left_text, right_text):
    width, height = doc.pagesize
    canvas.saveState()
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.5)
    canvas.line(34, 28, width - 34, 28)
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(FAINT)
    label = safe_text(left_text)
    if CURRENT_FILE:
        label += "  ·  " + safe_text(CURRENT_FILE)
    canvas.drawString(34, 17, label)
    canvas.drawRightString(width - 34, 17, f"{right_text}  ·  page {doc.page}")
    canvas.restoreState()


# ---------------------------------------------------------------------------
# PDF 1 — code
# ---------------------------------------------------------------------------

NEW_FILES = [
    ("roblox-bridge/tunnel_manager.py", "Supervised cloudflared/ngrok tunnel"),
    ("roblox-bridge/bus_core.py", "Multi-agent presence, mailbox and lease-based task board"),
    ("roblox-bridge/auth_core.py", "Scoped tokens, expiry, revocation, token-bucket limits"),
    ("roblox-bridge/telemetry.py", "Metrics, SSE event stream, hash-chained audit log"),
    ("roblox-bridge/tools/seed_agent_bus.py", "Idempotent Agent Bus seeding (online or offline)"),
    ("jobs/measure_tunnel_latency.py", "G2: p50/p95 tunnel latency measurement (stdlib only)"),
    ("jobs/live_acceptance.py", "G7: acceptance run with paste-ready evidence JSON"),
    ("jobs/drill_studio_kill.py", "G8: kill-switch drill for the job reaper"),
]

DIFF_FILES = [
    ("roblox-bridge/server.py", "HTTP bridge, auth scopes, ops endpoints, dashboard tab 7"),
    ("roblox-bridge/mcp_core.py", "agent_bus + bridge_ops tools, 3 resources, client helpers"),
    ("roblox-bridge/arena_studio.py", "ops / metrics / audit / tunnel / tokens / bus CLI"),
    ("roblox-bridge/setup.py", "Supervised tunnel launch + URL watcher"),
    ("roblox-bridge/test_bridge_mcp.py", "v5 test coverage (scopes, bus, reaping, audit, tunnel)"),
    ("roblox-bridge/mcp_server.py", "Version banner"),
    ("roblox-bridge/ab.ps1", "Version banner + ops examples"),
]


def resolve_base() -> str:
    """Diff base: explicit flag, else the commit this branch forked from main.

    Using HEAD~1 breaks as soon as the PDF pack itself is committed, which is why
    the fork point is the default.
    """
    for ref in ("origin/main", "origin/master", "main", "master"):
        merge_base = git(["merge-base", "HEAD", ref]).strip()
        if merge_base:
            return merge_base
    return git(["rev-parse", "HEAD~1"]).strip() or "HEAD~1"


def build_code_pdf(base: str, out_path: Path):
    styles = make_styles()
    doc = BaseDocTemplate(
        str(out_path), pagesize=landscape(A4),
        leftMargin=30, rightMargin=30, topMargin=34, bottomMargin=40,
        title="Tunneling v5 - Implementation Code", author="Arena Agent",
        subject="Roblox Studio MCP bridge v5 ops platform", creator="build_handoff_pdfs.py",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="body")
    doc.addPageTemplates([
        PageTemplate(id="cover", frames=[frame],
                     onPage=lambda c, d: cover_canvas(
                         c, d, "Tunneling v5", "Implementation code - supervised tunnel, agent bus, telemetry",
                         f"BRIDGE {VERSION}",
                         [
                             ("Branch", BRANCH),
                             ("Diff base", f"{base} -> HEAD"),
                             ("Generated", TODAY),
                             ("Tests", "35 passing (python3 -B roblox-bridge/test_bridge_mcp.py)"),
                             ("Runtime", "Python 3 stdlib only (no pip packages)"),
                         ],
                         "1 What this release contains · 2 File inventory · 3 How to apply and verify · "
                         "4 Complete sources (tunnel_manager, bus_core, auth_core, telemetry, seed_agent_bus) · "
                         "5 Unified diffs for every modified file · 6 Verification snapshot",
                     )),
        PageTemplate(id="body", frames=[frame],
                     onPageEnd=lambda c, d: page_chrome(
                         c, d, "Tunneling v5 - implementation code", "Clovetie/Tunneling")),
    ])
    doc.build(_code_story(styles, base))
    return out_path


def _code_story(styles, base):
    story = [NextPageTemplate("body"), PageBreak(), SetFile("")]

    story += [
        Paragraph("1. What this release contains", styles["h1"]),
        Paragraph(
            "v5 turns the local Studio bridge into an ops platform. The MCP tool surface grows to "
            "<b>70 tools</b> (adds <font name='Courier'>agent_bus</font> and <font name='Courier'>bridge_ops</font>) "
            "and <b>8 resources</b>. Four new stdlib modules carry the new behaviour, and the existing "
            "server/CLI/setup paths are extended without breaking v4 callers.", styles["body"]),
    ]
    bullets = [
        "<b>Supervised tunnel</b> - auto-detected cloudflared/ngrok, URL scraping, public health probes, backoff restarts, named tunnels, LAN URLs, and a hard refusal to publish a token-less bridge.",
        "<b>Agent Bus</b> - presence + mailbox + lease-based task board so multiple agents and humans can share one Studio session without collisions.",
        "<b>Scoped tokens</b> - hashed read/write/admin credentials with expiry and per-token rate limits; read tokens cannot mutate Studio over MCP or REST.",
        "<b>Resilient jobs</b> - idempotency keys, priorities, bounded retries, per-job timeouts and orphan requeue.",
        "<b>Observability</b> - Prometheus metrics, SSE event stream, SHA-256 hash-chained audit log, consolidated ops report, and dashboard tab 7.",
    ]
    for b in bullets:
        story.append(Paragraph(b, styles["bullet"], bulletText="-"))
    story += [Spacer(1, 8), Paragraph("2. File inventory", styles["h1"])]

    rows = [[Paragraph("<b>File</b>", styles["small"]), Paragraph("<b>Kind</b>", styles["small"]),
             Paragraph("<b>Purpose</b>", styles["small"])]]
    for path, why in NEW_FILES:
        rows.append([Paragraph(f"<font name='Courier'>{path}</font>", styles["small"]),
                     Paragraph("new", styles["small"]), Paragraph(why, styles["small"])])
    for path, why in DIFF_FILES:
        rows.append([Paragraph(f"<font name='Courier'>{path}</font>", styles["small"]),
                     Paragraph("diff", styles["small"]), Paragraph(why, styles["small"])])
    table = Table(rows, colWidths=[250, 50, doc_width_landscape() - 300])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), PANEL_ALT),
        ("BOX", (0, 0), (-1, -1), 0.5, RULE),
        ("INNERGRID", (0, 1), (-1, -1), 0.35, RULE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story += [table, Spacer(1, 10)]

    story += [
        Paragraph("3. How to apply and verify", styles["h1"]),
        Paragraph(
            "Copy the new files into <font name='Courier'>roblox-bridge/</font> and apply the diffs to the "
            "modified files (both sides are shown in this document). Then:", styles["body"]),
        Paragraph(
            "python3 -B roblox-bridge/test_bridge_mcp.py        # expect: Ran 35 tests ... OK<br/>"
            "python3 roblox-bridge/arena_studio.py ops           # peers, queue, tunnel, agents, tasks<br/>"
            "python3 roblox-bridge/arena_studio.py audit --verify # hash chain intact<br/>"
            "python3 setup.py --tunnel                           # supervised public tunnel",
            styles["mono"]),
        Spacer(1, 6),
        Paragraph(
            "Auth is unchanged for existing installs: <font name='Courier'>bridge.token</font> is the admin owner "
            "credential and nothing new is required. Runtime state (audit, tokens, bus, last tunnel URL) is written "
            "to the gitignored <font name='Courier'>roblox-bridge/bridge_state/</font>.", styles["body"]),
        Paragraph("4. New source files", styles["h1"]),
    ]

    new_rows = [[Paragraph("<b>File</b>", styles["small"]), Paragraph("<b>Lines</b>", styles["small"]),
                 Paragraph("<b>Purpose</b>", styles["small"])]]
    for path, purpose in NEW_FILES:
        full = ROOT / path
        if not full.is_file():
            continue
        new_rows.append([
            Paragraph(f"<font name='Courier'>{path}</font>", styles["small"]),
            Paragraph(str(len(full.read_text(encoding='utf-8').splitlines())), styles["small"]),
            Paragraph(purpose, styles["small"]),
        ])
    new_table = Table(new_rows, colWidths=[250, 45, doc_width_landscape() - 295])
    new_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), PANEL_ALT),
        ("BOX", (0, 0), (-1, -1), 0.5, RULE),
        ("INNERGRID", (0, 1), (-1, -1), 0.35, RULE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story += [new_table, Spacer(1, 8), Paragraph(
        "Complete sources follow first, then unified diffs for every modified file. "
        "Line numbers are left-margin references; wrapped lines carry no number.", styles["small"])]

    first = True
    for path, purpose in NEW_FILES:
        full = ROOT / path
        if not full.is_file():
            continue
        source = full.read_text(encoding="utf-8")
        lines = code_lines(source, PythonLexer())
        story += [PageBreak(), SetFile(path), _file_header(path, purpose, len(source.splitlines()), styles)]
        story += _code_blocks(lines, styles, per_block=60)
        first = False

    story += [PageBreak(), SetFile(""), Paragraph("5. Diffs against the previous release", styles["h1"]),
              Paragraph(
                  f"Unified diffs produced with <font name='Courier'>git diff {base} -- &lt;file&gt;</font>. "
                  "Lines prefixed <font name='Courier'>+</font> are added, <font name='Courier'>-</font> removed; "
                  "each file starts on its own page.", styles["body"])]

    diff_rows = [[Paragraph("<b>File</b>", styles["small"]), Paragraph("<b>Added</b>", styles["small"]),
                  Paragraph("<b>Removed</b>", styles["small"]), Paragraph("<b>Purpose</b>", styles["small"])]]
    total_added = total_removed = 0
    for path, purpose in DIFF_FILES:
        numstat = git(["diff", "--numstat", base, "--", path]).split()
        added = numstat[0] if len(numstat) >= 3 else "0"
        removed = numstat[1] if len(numstat) >= 3 else "0"
        if added.isdigit():
            total_added += int(added)
        if removed.isdigit():
            total_removed += int(removed)
        diff_rows.append([
            Paragraph(f"<font name='Courier'>{path}</font>", styles["small"]),
            Paragraph(f"<font color='#116329'>+{added}</font>", styles["small"]),
            Paragraph(f"<font color='#82071e'>-{removed}</font>", styles["small"]),
            Paragraph(escape_xml(purpose), styles["small"]),
        ])
    diff_rows.append([
        Paragraph("<b>Total</b>", styles["small"]),
        Paragraph(f"<b>+{total_added}</b>", styles["small"]),
        Paragraph(f"<b>-{total_removed}</b>", styles["small"]),
        Paragraph("", styles["small"]),
    ])
    diff_table = Table(diff_rows, colWidths=[250, 52, 62, doc_width_landscape() - 364])
    diff_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), PANEL_ALT),
        ("BACKGROUND", (0, -1), (-1, -1), PANEL_ALT),
        ("BOX", (0, 0), (-1, -1), 0.5, RULE),
        ("INNERGRID", (0, 1), (-1, -2), 0.35, RULE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story += [Spacer(1, 6), diff_table, Spacer(1, 8), Paragraph(
        "The diff sections carry the context lines needed to place each change; nothing outside these "
        "files is touched by the release.", styles["small"])]

    for path, purpose in DIFF_FILES:
        diff = git(["diff", "--no-color", "--unified=3", base, "--", path])
        if not diff.strip():
            diff = f"(no diff found for {path} against {base})"
        lines, kinds = code_lines(diff, DiffLexer(), with_kinds=True)
        story += [PageBreak(), SetFile(path + " (diff)"),
                  _file_header(path + "  (diff)", purpose, len(diff.splitlines()), styles)]
        story += _diff_blocks(list(zip(lines, kinds)), styles, per_block=60)

    story += [PageBreak(), SetFile(""), Paragraph("6. Verification snapshot", styles["h1"])]
    story += [Paragraph(
        "The v5 suite exercises the new surface end-to-end against the stateful mock Studio and a fake "
        "tunnel binary. Live Studio acceptance (real peer + real public tunnel) is still open and is tracked "
        "as G7/G8 in the task PDF.", styles["body"])]
    test_out = git(["show", "--stat", "--oneline", "HEAD"])
    story += [XPreformatted(escape_xml(safe_text(test_out.strip() or "commit info unavailable")),
                            ParagraphStyle("stat", fontName="Courier", fontSize=7.4, leading=9.4,
                                           textColor=INK, backColor=PANEL, borderPadding=6))]
    return story


def doc_width_landscape():
    return landscape(A4)[0] - 60


def _file_header(path: str, purpose: str, line_count: int, styles):
    table = Table(
        [[Paragraph(f"<font name='Courier' size='10'><b>{escape_xml(path)}</b></font>", styles["body"]),
          Paragraph(f"{line_count} lines", styles["small"])],
         [Paragraph(escape_xml(purpose), styles["small"]), ""]],
        colWidths=[doc_width_landscape() - 90, 90],
    )
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PANEL_ALT),
        ("BOX", (0, 0), (-1, -1), 0.5, RULE),
        ("SPAN", (0, 1), (1, 1)),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return KeepTogether([table, Spacer(1, 5)])


def _code_blocks(lines, styles, per_block=54):
    flow = []
    for index in range(0, len(lines), per_block):
        block = lines[index:index + per_block]
        flow.append(XPreformatted("\n".join(block), styles["code"]))
        if index + per_block < len(lines):
            flow.append(PageBreak())
    return flow


def _diff_blocks(items, styles, per_block=60):
    """One flowable per diff line so added/removed lines get a full-width tint."""
    flow = []
    for index in range(0, len(items), per_block):
        chunk = items[index:index + per_block]
        for markup, kind in chunk:
            style = styles["diff_add"] if kind == "+" else styles["diff_del"] if kind == "-" else styles["diff_line"]
            flow.append(Paragraph(markup, style))
        if index + per_block < len(items):
            flow.append(PageBreak())
    return flow


# ---------------------------------------------------------------------------
# PDF 2 — tasks for Gemini 4 Argon
# ---------------------------------------------------------------------------

TASKS = [
    {
        "id": "G1",
        "title": "Named-tunnel and custom-domain runbook",
        "priority": "High",
        "files": "roblox-bridge/tunnel_manager.py, docs/OPS.md (new section only)",
        "attach": "tunnel_manager.py, docs/OPS.md",
        "objective": (
            "Make the named Cloudflare tunnel path production-ready and document it for a non-expert. "
            "The bridge already composes `cloudflared tunnel --no-autoupdate run --token <T>` for named "
            "tunnels and `--url <local>` for quick tunnels. Harden the named path and write the runbook."
        ),
        "dod": [
            "Malformed/empty token fails fast with an actionable message (state `error`, no silent loop).",
            "`--tunnel-name` is validated or removed from the CLI surface if it has no effect.",
            "`docs/OPS.md` gains a step-by-step section: create tunnel, DNS/CNAME, token, run bridge, verify `/api/tunnel` shows a stable URL.",
            "A unit test asserts the composed command contains `run --token` and never `--url` for named tunnels.",
        ],
        "verify": [
            "python3 -B roblox-bridge/test_bridge_mcp.py TestV5OpsPlatform",
            "python3 roblox-bridge/arena_studio.py tunnel status",
        ],
        "prompt": (
            "You are working on the Arena Roblox Studio bridge, version 5.2.0, file roblox-bridge/tunnel_manager.py.\n"
            "Context: TunnelManager supervises cloudflared/ngrok. _command() returns:\n"
            "  named:   [binary, 'tunnel', '--no-autoupdate', 'run', '--token', token]\n"
            "  quick:   [binary, 'tunnel', '--url', 'http://127.0.0.1:<port>', '--no-autoupdate']\n"
            "  ngrok:   [binary, 'http', str(port), '--log', 'stdout', '--log-format', 'logfmt']\n"
            "Constraints: Python 3 stdlib only; never publish a token-less bridge; keep the state machine "
            "(stopped/starting/online/degraded/reconnecting/unavailable/error) and the /api/tunnel JSON shape.\n"
            "Do these three things:\n"
            "1. Validate the named token: reject empty/whitespace and obviously malformed values before spawn, "
            "   set state='error' with an actionable lastError, and log one line to logTail.\n"
            "2. Decide whether --tunnel-name should be used (Cloudflare ingress configures the hostname) or removed; "
            "   if kept, expose it only as display metadata, never as a CLI flag that changes routing.\n"
            "3. Write the runbook section for docs/OPS.md: 'Named tunnel with a stable hostname'. Include Cloudflare "
            "   Zero Trust steps (create tunnel, public hostname -> http://127.0.0.1:8077, copy token), the exact "
            "   commands (python setup.py --tunnel --tunnel-token $TOKEN), verification commands, and three "
            "   troubleshooting cases (401 on /mcp, trycloudflare URL still appearing, state=unavailable).\n"
            "Deliver: unified diff of tunnel_manager.py, the markdown section, and a unittest method that asserts "
            "the named command contains 'run --token' and not '--url'.\n"
            "Do not modify server.py, mcp_core.py, auth_core.py, bus_core.py, telemetry.py, or existing tests."
        ),
    },
    {
        "id": "G2",
        "title": "Public-tunnel latency budget (p50/p95)",
        "priority": "High",
        "files": "docs/OPS.md, jobs/ (measurement script)",
        "attach": "arena_studio.py, docs/OPS.md",
        "objective": (
            "Quantify the tunnel tax so users can decide whether a named tunnel or a local agent is worth it. "
            "Measure `/api/jobs` round trips for three Studio tools, localhost vs the public URL, 20 samples each."
        ),
        "dod": [
            "A small script (jobs/measure_tunnel_latency.py, stdlib only) that takes --url, --token, --runs and prints p50/p95/max.",
            "Runs against a live Studio peer; records JSON output alongside the numbers.",
            "`docs/OPS.md` gains a 'Tunnel latency budget' table: tool, local p50/p95, tunnel p50/p95, delta.",
            "States the sample size, date, provider/flavor and region honestly; no invented numbers.",
        ],
        "verify": [
            "python3 jobs/measure_tunnel_latency.py --runs 20 --url http://127.0.0.1:8077",
            "python3 roblox-bridge/arena_studio.py metrics --prometheus",
        ],
        "prompt": (
            "Write a stdlib-only measurement script for the Arena bridge at jobs/measure_tunnel_latency.py.\n"
            "It must POST to <url>/api/jobs with {'type': <tool>, 'payload': <args>, 'wait': 60, 'dm': 'edit'} and "
            "header 'Authorization: Bearer <token>' (or token query param), then time the round trip. Tools to "
            "measure: ping_studio {}, get_studio_state {}, script_read {'path': 'ServerScriptService'}.\n"
            "CLI: --url, --token, --runs (default 20), --tools, --json-out. Print per-tool count/p50/p95/max and a "
            "markdown table. Handle 401 and no-peer (status=error reason=no_peer) by failing with a clear message.\n"
            "Then give me the markdown section 'Tunnel latency budget' for docs/OPS.md, with placeholders only "
            "if measurements are unavailable, and an explicit note that numbers must be produced on the operator's "
            "machine because sandbox egress cannot always reach trycloudflare.com.\n"
            "Do not modify the bridge source; this is tooling plus documentation."
        ),
    },
    {
        "id": "G3",
        "title": "Ops tab responsive layout and SSE reconnect",
        "priority": "Normal",
        "files": "roblox-bridge/server.py (dashboard HTML/JS only)",
        "attach": "server.py (dashboard_html function), OPS.md section 5",
        "objective": (
            "The Ops tab works on desktop but crowds below 420 px, and the EventSource dies permanently on a "
            "server restart. Make it mobile-usable and self-healing."
        ),
        "dod": [
            "At 390x844 the six stat cards wrap to two per row; the task board and tunnel panels stack; no horizontal scroll.",
            "EventSource reconnects with exponential backoff (1s -> 30s) and the feed shows 'reconnecting' vs 'closed'.",
            "New button: 'Copy MCP config (tunnel)' producing a JSON snippet with the public /mcp URL and the current token.",
            "No Python changes in server.py outside dashboard_html(); node --check on the extracted script passes.",
        ],
        "verify": [
            "python3 -c \"import sys;sys.path.insert(0,'roblox-bridge');import server;print(len(server.dashboard_html()))\"",
            "node --check /tmp/dash.js   # after extracting the <script> block",
        ],
        "prompt": (
            "You are editing the single-file dashboard returned by dashboard_html() in roblox-bridge/server.py "
            "(Python raw string containing HTML/CSS/JS). Features: tabs, tick() polling /api/state every 1.5s, an "
            "Ops tab with tunnel controls, agent bus, metrics and an EventSource on /api/events.\n"
            "Return only the changed HTML/CSS/JS fragments, with enough surrounding context to apply them:\n"
            "1. CSS: at max-width 720px make .grid two columns, stack .two-col, reduce #tab-ops card padding, and "
            "prevent long tunnel URLs from forcing horizontal scroll (word-break: break-all; overflow-wrap:anywhere).\n"
            "2. JS: replace the single EventSource handler with connectEvents(delay) that reconnects with "
            "Math.min(delay*2, 30000) after onerror, updates #ev-btn to 'Reconnecting (Ns)' and resets on success. "
            "Never leak duplicate sources; closing must cancel timers.\n"
            "3. Add a 'Copy MCP config (tunnel)' button in the tunnel card. It copies "
            "{\"mcpServers\":{\"roblox-studio\":{\"url\":\"<publicUrl>/mcp\",\"headers\":{\"Authorization\":\"Bearer <token>\"}}}} "
            "using the current token input and the publicUrl from the last /api/ops response.\n"
            "Keep vanilla JS, no frameworks, no external assets. Ensure the extracted <script> passes node --check."
        ),
    },
    {
        "id": "G4",
        "title": "Multi-agent MCP prompt recipes",
        "priority": "Normal",
        "files": "roblox-bridge/mcp_core.py (PROMPTS and get_mcp_prompt only)",
        "attach": "mcp_core.py (PROMPTS, get_mcp_prompt), bus_core.py action list",
        "objective": (
            "Agents do not use the bus well without instructions. Add three workflow prompts that render live "
            "bus state and teach handoff, standup and triage."
        ),
        "dod": [
            "prompts/list returns 7 prompts: the existing 4 plus multi_agent_handoff, bus_standup, task_triage.",
            "Each prompt embeds the live roster/tasks when a bridge is reachable, and a safe fallback sentence otherwise.",
            "Prompt text tells the model to claim before editing, post results, and complete with artifacts.",
            "tests: prompts/list count and one prompts/get per new prompt with live bus state.",
        ],
        "verify": [
            "python3 -B roblox-bridge/test_bridge_mcp.py",
            "python3 roblox-bridge/arena_studio.py bus tasks",
        ],
        "prompt": (
            "In roblox-bridge/mcp_core.py there are 4 MCP prompts rendered by get_mcp_prompt(name, arguments). "
            "The bridge now has an agent bus (bus_core.AgentBus) with actions: register, agents, inbox, post, "
            "tasks, task_create, task_claim, task_complete, export. The MCP tool is agent_bus and the client "
            "exposes client.bus_action({...}).\n"
            "Add three prompts and their renderers:\n"
            "- multi_agent_handoff: instruct an agent to export the board (agent_bus action=export), summarise "
            "  in-progress leases and blockers, and post a handoff message with the next concrete step.\n"
            "- bus_standup: ask for what changed since a cursor, who owns what, and what is blocked; produce a "
            "  5-line standup.\n"
            "- task_triage: rank open tasks by priority and dependency depth, propose claim order, flag tasks "
            "  with no acceptance criteria.\n"
            "Render live state by calling client.bus_action for stats/agents/tasks inside the renderer, wrapped in "
            "try/except with a short fallback. Return: the PROMPTS entries, the get_mcp_prompt branches, and a "
            "unittest asserting prompts/list == 7 and each new prompt returns non-empty text. Change nothing else."
        ),
    },
    {
        "id": "G5",
        "title": "Stale-agent sweeper for the task board",
        "priority": "Normal",
        "files": "roblox-bridge/bus_core.py, roblox-bridge/test_bridge_mcp.py",
        "attach": "bus_core.py (leases/presence), telemetry.py (metrics)",
        "objective": (
            "A crashed or closed agent keeps its in_progress task until the lease expires, which can be 15 minutes. "
            "Release tasks held by agents that have been offline for a long time and tell the team."
        ),
        "dod": [
            "AgentBus.sweep_stale(offline_seconds=900) releases tasks owned by offline agents, sets status todo, "
            "clears the lease, records history and emits one broadcast alert per sweep.",
            "A counter-style stat (agentsReaped) is exposed through stats() and can be mirrored to "
            "bridge_agents_reaped_total by the caller.",
            "Does not touch leases whose owner is still online; idempotent when called twice.",
            "Unit test with injected timestamps proves release, alert, and no-op when online.",
        ],
        "verify": [
            "python3 -B roblox-bridge/test_bridge_mcp.py TestV5OpsPlatform",
        ],
        "prompt": (
            "In roblox-bridge/bus_core.py, AgentBus stores agents with lastSeen/ttl and tasks with "
            "lease={owner,claimedAt,expiresAt,seconds}. Implement sweep_stale(offline_seconds=900, now=None) that:\n"
            "1. Finds agents whose lastSeen is older than offline_seconds or whose online flag is False.\n"
            "2. Releases every task with status in_progress whose lease.owner (or assignee) is a stale agent: "
            "   status='todo', assignee=None, lease=None, history entry {action:'reaped'}, leaseExpiries+1.\n"
            "3. Appends ONE broadcast message per sweep of kind 'alert', subject 'Stale agent tasks released', "
            "   listing task ids and the agent names.\n"
            "4. Increments a counter exposed via stats() as agentsReaped and lastSweepAt.\n"
            "5. Is safe to call twice (no duplicate alerts, no double release).\n"
            "Use the existing lock and _save(); do not change handle() action names. Add AgentBus tests using an "
            "injectable now or by mutating lastSeen. Return the full method, the stats() diff, and the test method."
        ),
    },
    {
        "id": "G6",
        "title": "Incremental handoff brief (since / summaryOnly)",
        "priority": "Low",
        "files": "roblox-bridge/bus_core.py, tests",
        "attach": "bus_core.py export_handoff",
        "objective": (
            "Handoff briefs currently contain everything. Let them be incremental so long sessions stay readable."
        ),
        "dod": [
            "export_handoff(path=None, include_done=True, since=0, summary_only=False) filters messages and events by cursor and omits full task detail when summary_only.",
            "handle(action='export') passes since/summaryOnly through.",
            "Existing export test stays green; new test proves only later messages appear.",
        ],
        "verify": ["python3 -B roblox-bridge/test_bridge_mcp.py TestV5OpsPlatform"],
        "prompt": (
            "In roblox-bridge/bus_core.py, export_handoff renders a markdown brief from agents, tasks, messages and "
            "stats. Extend it with since: int = 0 and summary_only: bool = False.\n"
            "Behaviour: messages with seq <= since are omitted; the task board shows only tasks updated after since "
            "when since > 0, plus every currently open task regardless of age. summary_only renders one line per task "
            "(status, priority, owner, title) with no detail/result/artifacts and drops the messages section.\n"
            "Keep the returned dict {markdown, path, bytes}. Update handle() to pass p.get('since') and "
            "p.get('summaryOnly'). Return the full function replacement and a unittest that posts a message, captures "
            "the cursor, posts another, and asserts only the second appears with since=cursor."
        ),
    },
    {
        "id": "G7",
        "title": "Live Studio acceptance run (real peer + real tunnel)",
        "priority": "High",
        "files": "docs/OPS.md section 10 only",
        "attach": "none - needs the operator's PC",
        "objective": (
            "Prove the whole path with a real Roblox Studio place and a real public tunnel, then record the evidence. "
            "This is the gap the unit tests cannot close."
        ),
        "dod": [
            "`python setup.py --tunnel` prints the public URL; /api/tunnel shows state=online and health.ok=true.",
            "From an MCP client through the tunnel URL, bridge_ops status returns ok with studio_connected=true.",
            "self_test / MCP self_test reports studioScratch=pass.",
            "The JSON evidence (health, tunnel, self_test summary) is pasted under 'Live acceptance' in docs/OPS.md.",
        ],
        "verify": [
            "python3 setup.py --tunnel",
            "python3 roblox-bridge/arena_studio.py ops",
            "python3 roblox-bridge/arena_studio.py tunnel status",
            "python3 roblox-bridge/arena_studio.py audit --verify",
        ],
        "prompt": (
            "Here is the operator checklist for a live acceptance run of the Arena bridge 5.2.0. Turn it into a "
            "precise, copy-pasteable acceptance script with expected outputs and failure triage.\n"
            "Steps: (1) Studio open on a scratch place with the ArenaBridge.lua plugin enabled; (2) python setup.py "
            "--tunnel; (3) confirm /api/tunnel state=online, publicUrl non-empty, health.ok=true; (4) from an MCP "
            "client configured with that publicUrl and the bridge token call bridge_ops {action:'status'} and "
            "agent_bus {action:'register'}; (5) run self_test with includeStudioSmoke=true; (6) verify "
            "audit --verify ok; (7) stop Studio mid-job and confirm the reaper requeues (bridge_jobs_reaped_total "
            "increments, job returns to queued).\n"
            "For each step give: command, expected JSON fields, and what to do if it fails (401 -> token, no_peer -> "
            "plugin not polling, unavailable -> no cloudflared/ngrok, blocked -> no token). Finish with a markdown "
            "'Live acceptance' template for docs/OPS.md that includes date, Studio version, place id, provider, "
            "URL, and pasted JSON."
        ),
    },
    {
        "id": "G8",
        "title": "Kill-switch drill evidence",
        "priority": "Normal",
        "files": "docs/OPS.md section 10 only",
        "attach": "none - needs the operator's PC",
        "objective": (
            "Document what happens when Studio disappears mid-job, including the event feed and metrics before/after."
        ),
        "dod": [
            "A job submitted with dm=edit is running when Studio is closed.",
            "Within ~25 s the job returns to queued and bridge_jobs_reaped_total increments.",
            "After reopening Studio the job completes and the event sequence (job.enqueue, job.claim, job.requeued_orphan, job.claim, job.finish) is pasted as evidence.",
        ],
        "verify": [
            "python arena_studio.py metrics --prometheus | findstr bridge_jobs_reaped_total",
            "python arena_studio.py audit --limit 20",
        ],
        "prompt": (
            "Design a reproducible kill-switch drill for the Arena bridge 5.0.0 (reaper in server.py: jobs claimed by "
            "a peer that stops polling for >20s are requeued; jobs exceeding their timeout are failed).\n"
            "Give me: (1) an exact command sequence to start a long-running edit job, capture metrics before/after, "
            "and close Studio at the right moment; (2) the expected event sequence in /api/events; (3) the exact "
            "assertions (counter delta >= 1, job status transitions queued -> running -> queued -> running -> done); "
            "(4) a markdown evidence template for docs/OPS.md with placeholders for the pasted JSON; (5) three "
            "failure modes and how to distinguish them (job stuck running, job failed permanently, peer never "
            "returns). Keep it runnable with arena_studio.py and curl/PowerShell only."
        ),
    },
]


STATUS = {
    "G1": "Landed in 5.1.0 (token validation + runbook in docs/OPS.md 1.1). Re-run the prompt to audit it.",
    "G2": "Script landed (jobs/measure_tunnel_latency.py); the numbers still need an operator run.",
    "G3": "Landed in 5.1.0 (responsive Ops tab, reconnecting EventSource, copy MCP config).",
    "G4": "Landed in 5.1.0 (7 prompts; live state + offline fallback).",
    "G5": "Landed in 5.1.0 (agent_bus sweep, counters, one alert per sweep).",
    "G6": "Landed in 5.1.0 (export since / summaryOnly).",
    "G7": "Operator-only. Script shipped (jobs/live_acceptance.py); evidence still pending a Studio peer.",
    "G8": "Operator-only. Script shipped (jobs/drill_studio_kill.py); evidence still pending a Studio peer.",
}


CONTEXTS = {
    "G1": (
        "The named-tunnel path exists but has only ever been exercised with a fake binary. Nothing in the "
        "repo proves the composed command is correct for Cloudflare's named-tunnel mode, and the operator "
        "docs stop at quick tunnels."
    ),
    "G2": (
        "Every support question about the tunnel comes down to latency, and the project has no numbers. "
        "The sandbox that wrote this bridge cannot reach trycloudflare.com, so the numbers must come from "
        "the operator's machine; the script and the table are still the model's job."
    ),
    "G3": (
        "The Ops tab is one large HTML string inside server.py. It was built desktop-first, and the "
        "EventSource has no reconnect path, so a bridge restart silently freezes the live feed."
    ),
    "G4": (
        "The bus works, but agents arrive without a protocol: they claim nothing, post nothing, and leave "
        "work half-done. MCP prompts are the cheapest place to teach the protocol because they render "
        "inside the client."
    ),
    "G5": (
        "Leases expire after 900 s, so an agent that dies keeps its task locked for a quarter of an hour "
        "and the board looks busy while nothing moves. A sweeper closes that gap without weakening leases."
    ),
    "G6": (
        "Handoff briefs grow with the session, so by the twentieth message they are longer than the work "
        "they describe. Cursor-based export keeps them usable."
    ),
    "G7": (
        "This is the only task that cannot be finished from a text model's output alone: it needs a real "
        "Studio session and a real public URL. The model's deliverable is the script and the expected "
        "outputs; the operator's deliverable is the pasted evidence."
    ),
    "G8": (
        "The kill switch is the feature that makes the tunnel safe to leave on. It has unit coverage for "
        "the guard but no documented drill, so nobody knows what a broken guard looks like in practice."
    ),
}


SCOPE_LOCK = {
    "G1": "server.py, mcp_core.py, auth_core.py, bus_core.py, telemetry.py, existing tests",
    "G2": "bridge source (this task is tooling + docs only)",
    "G3": "any Python in server.py outside dashboard_html()",
    "G4": "bus_core.py, server.py, existing prompts and MCP tools",
    "G5": "handle() action names, lease rules for agents that are still online",
    "G6": "the current export signature/defaults, other bus actions",
    "G7": "bridge source (docs section 10 only)",
    "G8": "bridge source (docs section 10 only)",
}


def build_tasks_pdf(out_path: Path):
    styles = make_styles()
    doc = BaseDocTemplate(
        str(out_path), pagesize=A4,
        leftMargin=40, rightMargin=40, topMargin=42, bottomMargin=44,
        title="Gemini 4 Argon - v5 task pack", author="Arena Agent",
        subject="Prompt-ready tasks for the Tunneling v5 bridge", creator="build_handoff_pdfs.py",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="body")
    doc.addPageTemplates([
        PageTemplate(id="cover", frames=[frame],
                     onPage=lambda c, d: cover_canvas(
                         c, d, "Partner task pack", "8 prompts for Gemini 4 Argon - the operator runs them",
                         f"8 TASKS  ·  BRIDGE {VERSION}",
                         [
                             ("For", "Gemini 4 Argon (model, not an agent)"),
                             ("From", "Arena coding agent, branch " + BRANCH),
                             ("Repo", "Clovetie/Tunneling"),
                             ("Generated", TODAY),
                             ("How to use", "Paste one prompt + the attached files into a session"),
                         ],
                         "How to use this pack · Task index · Ground rules · Operator coordination · "
                         "G1-G8 task cards (objective, definition of done, verification, verbatim prompt, "
                         "report-back template) · Appendix: seeded bus tasks",
                     )),
        PageTemplate(id="body", frames=[frame],
                     onPageEnd=lambda c, d: page_chrome(
                         c, d, "Gemini 4 Argon - v5 task pack", "Tunneling")),
    ])
    doc.build(_tasks_story(styles))
    return out_path


def _tasks_story(styles):
    content_width = A4[0] - 80  # matches the 40pt left/right margins
    story = [NextPageTemplate("body"), PageBreak()]

    story += [
        Paragraph("How to use this pack", styles["h1"]),
        Paragraph(
            "Gemini 4 Argon is a <b>model</b>, not an autonomous agent: it cannot run commands, open a repo, "
            "claim a bus task, or push a branch. So each task below is a <b>self-contained prompt</b> plus the "
            "files it needs. The human operator (or an agent wrapper) does the running: attach the listed files, "
            "paste the prompt, take the returned patch/docs, run the verification commands, and record the result "
            "on the Agent Bus.", styles["body"]),
        Paragraph(
            "G1 to G6 have since been implemented on this branch (bridge 5.2.0, 35 tests); their prompts are kept "
            "verbatim so a second model can re-derive or audit the same work. G7 and G8 need a live Studio peer: "
            "the scripts ship in <font name='Courier'>jobs/</font>, the evidence does not.", styles["body"]),
    ]
    for tip in [
        "<b>One task per session.</b> Attach only the files listed under 'Attach' to keep context focused.",
        "<b>Paste the prompt block verbatim.</b> It already names files, constraints and the exact deliverables.",
        "<b>Reject invented measurements.</b> G2 and G7/G8 require real numbers from a machine that can reach Studio; placeholders must stay visible.",
        "<b>Apply and verify.</b> Run the verification commands and `python3 -B roblox-bridge/test_bridge_mcp.py`; the suite must stay green (35 tests).",
        "<b>Record it.</b> Ask the operator to claim the task on the bus, then complete it with artifacts (files touched, test output).",
    ]:
        story.append(Paragraph(md(tip), styles["bullet"], bulletText="-"))

    story += [Spacer(1, 6), Paragraph("Task index", styles["h1"])]
    rows = [[Paragraph("<b>ID</b>", styles["small"]), Paragraph("<b>Task</b>", styles["small"]),
             Paragraph("<b>Priority</b>", styles["small"]), Paragraph("<b>Owner files</b>", styles["small"])]]
    for task in TASKS:
        rows.append([
            Paragraph(task["id"], styles["small"]),
            Paragraph(task["title"], styles["small"]),
            Paragraph(task["priority"], styles["small"]),
            Paragraph(f"<font name='Courier' size='6.8'>{escape_xml(task['files'])}</font>", styles["small"]),
        ])
    table = Table(rows, colWidths=[26, 178, 44, content_width - 248])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), PANEL_ALT),
        ("BOX", (0, 0), (-1, -1), 0.5, RULE),
        ("INNERGRID", (0, 1), (-1, -1), 0.35, RULE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story += [table]

    story += [Spacer(1, 10), Paragraph("What each card contains", styles["h1"])]
    for item in [
        "<b>Owner files</b> - the only files the patch may touch. Anything else is out of scope for that task.",
        "<b>Context</b> - the background the model cannot infer from the attachment alone.",
        "<b>Objective</b> - one paragraph, no acceptance criteria hidden inside it.",
        "<b>Definition of done</b> - the checklist the operator applies before accepting a patch.",
        "<b>Verification</b> - the exact commands that must pass, with the expected result.",
        "<b>Paste-ready prompt</b> - the block to paste verbatim, attachments attached and nothing else added.",
        "<b>Report back</b> - the template the operator fills in and posts to the Agent Bus.",
    ]:
        story.append(Paragraph(md(item), styles["bullet"], bulletText="-"))

    story += [PageBreak(), Paragraph("Ground rules for every task", styles["h1"])]
    for rule in [
        "Python 3 standard library only. The bridge must stay pip-free at runtime (reportlab/pygments are used only to build these PDFs).",
        "Never weaken auth. `bridge.token` is the admin owner credential; scoped tokens are additive.",
        "Never let the bridge publish itself without a token; the tunnel guard must keep returning state='blocked'.",
        "Runtime state belongs in gitignored `roblox-bridge/bridge_state/`; never commit tokens, audit logs or bus data.",
        "Keep the JSON shapes stable: `/api/health`, `/api/tunnel`, `/api/ops`, `/api/metrics`, `/api/bus`, `/api/audit`.",
        "Do not rename existing MCP tools or bus actions; additive changes only.",
        "Every change keeps `python3 -B roblox-bridge/test_bridge_mcp.py` green (35 tests).",
        "Report honestly: if a step needs a live Roblox Studio peer, say it is unverified rather than claiming success.",
    ]:
        story.append(Paragraph(md(rule), styles["bullet"], bulletText="-"))

    story += [Spacer(1, 8), Paragraph("Operator coordination (Agent Bus)", styles["h1"]),
              Paragraph(
                  "The model cannot claim tasks, so the operator does it before pasting the prompt, and completes it "
                  "afterwards with the artifacts. Commands:", styles["body"]),
              XPreformatted(escape_xml(safe_text(
                  "python roblox-bridge/arena_studio.py bus tasks\n"
                  "python roblox-bridge/arena_studio.py bus task-claim  --task task-xxxxxxxx --agent agent-xxxxxxxx\n"
                  "python roblox-bridge/arena_studio.py bus task-complete --task task-xxxxxxxx --agent agent-xxxxxxxx \\\n"
                  "    --result \"G1 docs + test landed\" --detail \"see tunnel_manager.py diff\"\n"
                  "python roblox-bridge/arena_studio.py bus post --agent agent-xxxxxxxx \\\n"
                  "    --to \"Claude\" --subject \"G1 review\" --body \"patch attached, tests green\""
              )), styles["code"]),
              Paragraph(
                  "Seeding the whole work order into a fresh bridge: "
                  "<font name='Courier'>python roblox-bridge/tools/seed_agent_bus.py</font> "
                  "(reads <font name='Courier'>handoff/bus_seed.json</font>).", styles["body"])]

    story += [Spacer(1, 8), Paragraph("If the output cannot be applied", styles["h1"])]
    for item in [
        "Return the task to the model with the failing command and the first 40 lines of output; do not let it guess.",
        "If two attempts fail on the same file, split the task or ask for the design decision only, with no patch.",
        "If a task turns out to need live Studio access (G7, G8), stop and hand it back to the operator instead of simulating results.",
        "Never mark a bus task done on the model's word; the verification commands decide.",
    ]:
        story.append(Paragraph(md(item), styles["bullet"], bulletText="-"))

    for task in TASKS:
        story += [PageBreak()]
        badge = colors.HexColor({"High": "#cf222e", "Normal": "#0969da", "Low": "#57606a"}[task["priority"]])
        header = Table(
            [[Paragraph(f"<font size='18'><b>{task['id']}</b></font>", styles["body"]),
              Paragraph(f"<font size='13'><b>{escape_xml(task['title'])}</b></font>", styles["body"]),
              Paragraph(f"<font color='#{badge.hexval()[2:]}'><b>{task['priority']}</b></font>", styles["body"])]],
            colWidths=[42, content_width - 108, 66],
        )
        header.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), PANEL_ALT),
            ("BOX", (0, 0), (-1, -1), 0.6, RULE),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ]))
        story += [header, Spacer(1, 6)]

        meta = Table([
            [Paragraph("<b>Owner files</b>", styles["small"]),
             Paragraph(f"<font name='Courier' size='7'>{escape_xml(task['files'])}</font>", styles["small"])],
            [Paragraph("<b>Attach to prompt</b>", styles["small"]),
             Paragraph(escape_xml(task["attach"]), styles["small"])],
            [Paragraph("<b>Out of scope</b>", styles["small"]),
             Paragraph(md(SCOPE_LOCK.get(task["id"], "")), styles["small"])],
            [Paragraph("<b>Status</b>", styles["small"]),
             Paragraph(md(STATUS.get(task["id"], "")), styles["small"])],
        ], colWidths=[92, content_width - 92])
        meta.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LINEBELOW", (0, 0), (-1, -2), 0.3, RULE),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story += [meta, Spacer(1, 4)]

        story += [Paragraph("Context", styles["h2"]),
                  Paragraph(md(CONTEXTS.get(task["id"], "")), styles["body"]),
                  Paragraph("Objective", styles["h2"]), Paragraph(md(task["objective"]), styles["body"]),
                  Paragraph("Definition of done", styles["h2"])]
        for item in task["dod"]:
            story.append(Paragraph(md(item), styles["bullet"], bulletText="-"))

        story += [KeepTogether([
            Paragraph("Verification", styles["h2"]),
            XPreformatted(escape_xml(wrap_plain("\n".join(task["verify"]), 104)), styles["code_small"]),
        ])]

        prompt_box = Table([[XPreformatted(escape_xml(wrap_plain(task["prompt"], 104)), styles["prompt"])]],
                           colWidths=[content_width])
        prompt_box.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.6, RULE),
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fbfcfd")),
            ("LEFTPADDING", (0, 0), (-1, -1), 9),
            ("RIGHTPADDING", (0, 0), (-1, -1), 9),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]))
        story += [KeepTogether([Paragraph("Paste-ready prompt", styles["h2"]), prompt_box]), Spacer(1, 8)]

        story += [KeepTogether([Paragraph("Report back", styles["h2"]),
                  XPreformatted(escape_xml(safe_text(
                      "Result      : pass / pass-with-caveats / blocked\n"
                      "Files       : <paths touched by the applied patch>\n"
                      "Tests       : python3 -B roblox-bridge/test_bridge_mcp.py -> Ran 35 tests ... OK\n"
                      "Evidence    : <diff / JSON / doc section>\n"
                      "Unverified  : <anything that needs live Studio or a real tunnel>\n"
                      "Open risks  :"
                  )), styles["code_small"])])]

    # Appendix: seed data as tables (the JSON file stays the machine-readable source)
    story += [PageBreak(), Paragraph("Appendix - seeded bus tasks", styles["h1"]),
              Paragraph(
                  "These are the board entries written by "
                  "<font name='Courier'>tools/seed_agent_bus.py</font> from "
                  "<font name='Courier'>handoff/bus_seed.json</font>. The JSON file remains the "
                  "machine-readable source; the tables below are the same content in reviewable form.",
                  styles["body"])]

    seed_path = OUT_DIR / "bus_seed.json"
    try:
        seed = json.loads(seed_path.read_text(encoding="utf-8")) if seed_path.is_file() else {}
    except json.JSONDecodeError:
        seed = {}

    agents = seed.get("agents") or []
    if agents:
        story += [Paragraph("Agents", styles["h2"])]
        rows = [[Paragraph("<b>Name</b>", styles["small"]), Paragraph("<b>Role</b>", styles["small"]),
                 Paragraph("<b>Capabilities</b>", styles["small"])]]
        for agent in agents:
            rows.append([
                Paragraph(md(agent.get("name", "")), styles["small"]),
                Paragraph(md(agent.get("role", "")), styles["small"]),
                Paragraph(md(", ".join(agent.get("capabilities") or [])), styles["small"]),
            ])
        table = Table(rows, colWidths=[110, 80, content_width - 190])
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), PANEL_ALT),
            ("BOX", (0, 0), (-1, -1), 0.5, RULE),
            ("INNERGRID", (0, 1), (-1, -1), 0.35, RULE),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story += [table, Spacer(1, 6)]

    tasks = seed.get("tasks") or []
    if tasks:
        story += [Paragraph("Tasks", styles["h2"])]
        rows = [[Paragraph("<b>ID</b>", styles["small"]), Paragraph("<b>Pri</b>", styles["small"]),
                 Paragraph("<b>Title</b>", styles["small"]), Paragraph("<b>Detail</b>", styles["small"])]]
        for task in tasks:
            rows.append([
                Paragraph(md(task.get("tags", ["-"])[-1]), styles["small"]),
                Paragraph(md((task.get("priority") or "").title()), styles["small"]),
                Paragraph(md(task.get("title", "")), styles["small"]),
                Paragraph(md(task.get("detail", "")), styles["small"]),
            ])
        table = Table(rows, colWidths=[24, 44, 130, content_width - 198], repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), PANEL_ALT),
            ("BOX", (0, 0), (-1, -1), 0.5, RULE),
            ("INNERGRID", (0, 1), (-1, -1), 0.35, RULE),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story += [table]

    return story


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default=None,
                    help="Diff base commit (default: merge-base with origin/main, else HEAD~1)")
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    ap.add_argument("--code-only", action="store_true")
    ap.add_argument("--tasks-only", action="store_true")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = args.base or resolve_base()
    base = base[:40]

    built = []
    if not args.tasks_only:
        code_pdf = out_dir / "Tunneling-v5-Code.pdf"
        build_code_pdf(base, code_pdf)
        built.append(code_pdf)
    if not args.code_only:
        tasks_pdf = out_dir / "Gemini-4-Argon-Tasks.pdf"
        build_tasks_pdf(tasks_pdf)
        built.append(tasks_pdf)

    for path in built:
        size_kb = path.stat().st_size / 1024
        print(f"wrote {path.relative_to(ROOT)}  ({size_kb:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
