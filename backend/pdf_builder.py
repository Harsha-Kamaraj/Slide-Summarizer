"""Turn the Markdown summary from summarize.py into a formatted PDF.

Two steps, kept separate so each is easy to test:
  1. parse_summary(): Markdown text -> Summary object (title, overview, ...)
  2. build_pdf():     Summary -> PDF bytes, laid out with reportlab
"""

import html
import io
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image,
    PageBreak,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from backend import config, figures

logger = logging.getLogger(__name__)


@dataclass
class Topic:
    heading: str
    explanation: str = ""  # optional paragraph before the points
    # Bullets are stored without their marker; numbered steps keep "1. ",
    # formulas keep "Formula: " and tables are "Table: " + one row per line,
    # so render time knows how to draw each one.
    points: list[str] = field(default_factory=list)


@dataclass
class Summary:
    title: str = ""
    overview: str = ""
    topics: list[Topic] = field(default_factory=list)
    terms: list[tuple[str, str]] = field(default_factory=list)
    # Optional extras (Stage 7)
    exam_questions: list[tuple[str, str]] = field(default_factory=list)  # (question, answer)
    flashcards: list[tuple[str, str]] = field(default_factory=list)  # (front, back)
    figure_key: str = ""


# --- 1. Parse ------------------------------------------------------------


def parse_summary(markdown: str) -> Summary:
    """Read the fixed-heading Markdown format. Lenient on purpose: small
    models drift on details like bold markers or bullet style."""
    summary = Summary()
    marker = figures.MARKER.search(markdown)
    if marker:
        summary.figure_key = marker.group(1)
        markdown = figures.MARKER.sub("", markdown)
    section = ""
    overview_lines: list[str] = []

    for raw in markdown.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("# "):
            summary.title = _strip_bold(line[2:])
        elif line.startswith("## "):
            section = line[3:].strip().lower()
        elif line.startswith("### ") and section == "key points":
            summary.topics.append(Topic(heading=_strip_bold(line[4:])))
        elif section == "overview":
            overview_lines.append(line)
        elif section == "key points":
            if not summary.topics:  # content before any ### heading
                summary.topics.append(Topic(heading="Key points"))
            topic = summary.topics[-1]
            if line.startswith("|"):
                _add_table_row(topic, line)
                continue
            text = _bullet_text(line) if _is_bullet(line) else line
            # Models sometimes wrap formulas in backticks; the box is enough.
            unticked = text.strip("`").strip()
            if unticked.lower().startswith("formula:"):
                formula = unticked[8:].strip().strip("`").strip()
                # Models may fill an optional formula slot with a placeholder.
                # Omit it even for older cached summaries, keeping real formulas.
                if _strip_bold(formula).strip().rstrip(".").casefold() not in {
                    "",
                    "none",
                    "n/a",
                    "not applicable",
                    "no formula",
                    "no formulas",
                }:
                    topic.points.append("Formula: " + formula)
            elif _is_bullet(line):
                topic.points.append(text)
            elif _NUMBERED.match(line):
                topic.points.append(line)
            else:  # a plain line is part of the topic's explanation paragraph
                topic.explanation = f"{topic.explanation} {line}".strip()
        elif section == "important terms" and _is_bullet(line):
            summary.terms.append(_split_term(_bullet_text(line)))
        elif section == "likely exam questions":
            _parse_exam_line(line, summary.exam_questions)
        elif section == "flashcards":
            card = _split_flashcard(_bullet_text(line) if _is_bullet(line) else line)
            if card:
                summary.flashcards.append(card)

    summary.overview = " ".join(overview_lines)
    return summary


_NUMBERED = re.compile(r"(\d+)\.\s+(.*)")
_TABLE_SEPARATOR = re.compile(r":?-{2,}:?")


def _add_table_row(topic: Topic, line: str) -> None:
    """Collect a Markdown table ("| a | b |" lines) into one "Table:" item,
    in its place among the bullets. Separator rows ("|---|") are skipped."""
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    if all(_TABLE_SEPARATOR.fullmatch(cell) for cell in cells if cell):
        return
    row = " | ".join(cells)
    if topic.points and topic.points[-1].startswith("Table: "):
        topic.points[-1] += "\n" + row
    else:
        topic.points.append("Table: " + row)


_ANSWER = re.compile(r"^(?:[-*]\s*)?\**answer\**\s*:\**\s*(.*)", re.IGNORECASE)


def _parse_exam_line(line: str, questions: list[tuple[str, str]]) -> None:
    """ "1. question" starts a question; "Answer: ..." fills in its answer. Any
    other line continues whichever came last, e.g. a question that lists a
    small dataset to trace an algorithm on (kept on its own line)."""
    answer = _ANSWER.match(line)
    numbered = _NUMBERED.match(line)
    if answer and questions:
        question, _ = questions[-1]
        questions[-1] = (question, answer.group(1).strip())
    elif numbered:
        questions.append((_strip_bold(numbered.group(2)), ""))
    elif questions:
        question, existing = questions[-1]
        extra = _bullet_text(line) if _is_bullet(line) else line
        if existing:
            questions[-1] = (question, f"{existing} {extra}")
        else:
            questions[-1] = (f"{question}\n{extra}", "")


def _split_flashcard(text: str) -> tuple[str, str] | None:
    # "Q: front | A: back". Bold markers go first: models write both
    # "**Q**:" and "**Q:**".
    match = re.match(r"Q\s*:\s*(.+?)\s*\|\s*A\s*:\s*(.+)", _strip_bold(text), re.IGNORECASE)
    return (match.group(1).strip(), match.group(2).strip()) if match else None


def _is_bullet(line: str) -> bool:
    return line[:2] in ("- ", "* ", "• ")


def _bullet_text(line: str) -> str:
    return line[2:].strip()


def _strip_bold(text: str) -> str:
    return text.replace("**", "").strip()


def _split_term(text: str) -> tuple[str, str]:
    # Accept both "**Term**: definition" and "Term: definition".
    match = re.match(r"\*\*(.+?)\*\*\s*:?\s*(.*)", text)
    if match:
        # rstrip(":") handles "**Term:** definition" (colon inside the bold).
        return match.group(1).strip().rstrip(":"), match.group(2).strip()
    term, sep, definition = text.partition(":")
    if sep:
        return term.strip(), definition.strip()
    return text, ""


# --- 2. Render -----------------------------------------------------------


def build_pdf(summary: Summary, source_name: str = "") -> bytes:
    """Lay out the summary as an A4 PDF and return its bytes. Returning bytes
    (not writing a file) lets the API send it straight back in Stage 3."""
    regular, bold = _register_fonts()
    styles = _styles(regular, bold)

    story = [Paragraph(_inline(summary.title or "Lecture Summary"), styles["title"])]
    subtitle = "AI-generated summary. Check important details against the original slides."
    if source_name:
        subtitle = f"From {html.escape(source_name)} · {subtitle}"
    story += [Paragraph(subtitle, styles["subtitle"]), Spacer(1, 6 * mm)]

    if summary.overview:
        story += [
            Paragraph("Overview", styles["h2"]),
            Paragraph(_inline(summary.overview), styles["body"]),
        ]

    if summary.topics:
        story.append(Paragraph("Key Points", styles["h2"]))
        for topic in summary.topics:
            head = [Paragraph(_inline(topic.heading), styles["h3"])]
            if topic.explanation:
                head.append(Paragraph(_inline(topic.explanation), styles["explanation"]))
            points = [_point(p, styles) for p in topic.points]
            # Keep a topic heading on the same page as its explanation and
            # first point, so a heading never sits alone at a page bottom.
            story.append(KeepTogether(head + points[:1]))
            story += points[1:]

    if summary.terms:
        story.append(Paragraph("Important Terms", styles["h2"]))
        rows = [
            [Paragraph(_inline(term), styles["term"]), Paragraph(_inline(defn), styles["body"])]
            for term, defn in summary.terms
        ]
        table = Table(rows, colWidths=[45 * mm, None])
        table.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LINEBELOW", (0, 0), (-1, -2), 0.25, colors.lightgrey),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        story.append(table)

    if summary.exam_questions:
        story.append(Paragraph("Likely Exam Questions", styles["h2"]))
        for number, (question, answer) in enumerate(summary.exam_questions, start=1):
            block = [
                Paragraph(
                    f"<b>{_inline(question)}</b>".replace("\n", "<br/>"),
                    styles["point"],
                    bulletText=f"{number}.",
                )
            ]
            if answer:
                block.append(Paragraph(f"Answer: {_inline(answer)}", styles["answer"]))
            # Keep each question with its answer.
            story.append(KeepTogether(block))

    if summary.flashcards:
        story.append(Paragraph("Flashcards", styles["h2"]))
        rows = [
            [
                Paragraph("<b>Question</b>", styles["body"]),
                Paragraph("<b>Answer</b>", styles["body"]),
            ]
        ]
        rows += [
            [Paragraph(_inline(front), styles["body"]), Paragraph(_inline(back), styles["body"])]
            for front, back in summary.flashcards
        ]
        cards = Table(rows, colWidths=[85 * mm, None], repeatRows=1)
        cards.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef4fb")),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.lightgrey),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        story.append(cards)

    if summary.figure_key:
        bundle = figures.load(summary.figure_key)
        story += [PageBreak(), Paragraph("Original Visual Slides", styles["h2"])]
        if bundle:
            story.append(
                Paragraph(
                    f"{len(bundle['slides'])} of {bundle['total']} detected visual slides. "
                    "Original slide images, not AI drawings. Refer to the numbered source slide for context.",
                    styles["body"],
                )
            )
            for i, slide in enumerate(bundle["slides"]):
                if i:
                    story.append(PageBreak())
                story.append(
                    Paragraph(_inline(f"Slide {slide['number']}: {slide['title']}"), styles["h3"])
                )
                picture = Image(io.BytesIO(slide["png"]))
                max_height = (175 if slide.get("caption") else 205) * mm
                scale = min(170 * mm / picture.imageWidth, max_height / picture.imageHeight)
                picture.drawWidth = picture.imageWidth * scale
                picture.drawHeight = picture.imageHeight * scale
                story.append(picture)
                if slide.get("caption"):
                    story.append(
                        Paragraph(
                            "Slide notes (excerpt): " + _inline(slide["caption"]), styles["body"]
                        )
                    )
        else:
            story.append(
                Paragraph(
                    "Saved slide images are unavailable. Consult the original deck for diagrams.",
                    styles["body"],
                )
            )

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        title=summary.title,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
    )
    doc.build(story, onFirstPage=_footer(regular), onLaterPages=_footer(regular))
    return buffer.getvalue()


def _inline(text: str) -> str:
    """Make model text safe for reportlab Paragraphs, which parse a mini-HTML.
    Escape first (a formula like "<P,T,E>" would otherwise be read as a tag),
    then turn **bold** back into real bold."""
    escaped = html.escape(text, quote=False)
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)


def _point(text: str, styles: dict[str, ParagraphStyle]):
    """A bullet ("•"), numbered step ("1."), boxed formula or table."""
    if text.startswith("Formula: "):
        return _formula_box(text[len("Formula: ") :], styles["formula"])
    if text.startswith("Table: "):
        return _table(text[len("Table: ") :], styles["cell"])
    numbered = _NUMBERED.match(text)
    if numbered:
        return Paragraph(
            _bold_label(_inline(numbered.group(2))),
            styles["point"],
            bulletText=f"{numbered.group(1)}.",
        )
    return Paragraph(_bold_label(_inline(text)), styles["point"], bulletText="•")


def _table(rows_text: str, style: ParagraphStyle) -> Table:
    """A slide table as a real table: header row shaded, columns share the
    page width."""
    rows = [row.split(" | ") for row in rows_text.splitlines()]
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]  # ragged rows
    data = [[Paragraph(_inline(cell), style) for cell in row] for row in rows]
    table = Table(data, colWidths=[160 * mm / width] * width, repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.4, colors.lightgrey),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef4fb")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    table.spaceBefore, table.spaceAfter = 3, 5
    return table


def _formula_box(formula: str, style: ParagraphStyle) -> Table:
    """Draw a formula centred in a shaded, bordered box so it stands out."""
    box = Table([[Paragraph(_formula_markup(formula), style)]], colWidths=[150 * mm])
    box.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#eef4fb")),
                ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#1f4e79")),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    box.spaceBefore, box.spaceAfter = 3, 5
    return box


def _formula_markup(formula: str) -> str:
    """Turn plain-text math into reportlab markup: "2^(2^d)" -> 2 with a
    superscript, "x_i" -> x with a subscript. Innermost brackets first, so
    nested powers like 2^(2^d) come out nested."""
    text = html.escape(formula, quote=False)
    text = re.sub(r"\s*([\^_])\s*", r"\1", text)  # "2 ^ (x)" -> "2^(x)"
    for marker, tag in (("^", "super"), ("_", "sub")):
        bracketed = re.compile(re.escape(marker) + r"\(([^()]*)\)")
        while bracketed.search(text):
            text = bracketed.sub(rf"<{tag}>\1</{tag}>", text)
        text = re.sub(re.escape(marker) + r"([A-Za-z0-9]+)", rf"<{tag}>\1</{tag}>", text)
    return text


def _bold_label(text: str) -> str:
    """Bold a short leading label ("Feature Space: ...") so bullets are easy to scan."""
    label, sep, rest = text.partition(":")
    if sep and rest and len(label.split()) <= 5 and "<b>" not in label:
        return f"<b>{label}:</b>{rest}"
    return text


def _register_fonts() -> tuple[str, str]:
    """Use DejaVu Sans if available, else fall back to built-in Helvetica."""
    if "DejaVuSans" in pdfmetrics.getRegisteredFontNames():
        return "DejaVuSans", "DejaVuSans-Bold"
    for folder in config.FONT_DIRS:
        regular, bold = Path(folder) / "DejaVuSans.ttf", Path(folder) / "DejaVuSans-Bold.ttf"
        if regular.exists() and bold.exists():
            pdfmetrics.registerFont(TTFont("DejaVuSans", str(regular)))
            pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", str(bold)))
            pdfmetrics.registerFontFamily("DejaVuSans", normal="DejaVuSans", bold="DejaVuSans-Bold")
            return "DejaVuSans", "DejaVuSans-Bold"
    logger.warning("DejaVu Sans not found; using Helvetica (Greek/math symbols may not render)")
    return "Helvetica", "Helvetica-Bold"


def _styles(regular: str, bold: str) -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    accent = colors.HexColor("#1f4e79")
    return {
        "title": ParagraphStyle(
            "title", parent=base["Title"], fontName=bold, fontSize=20, leading=24, textColor=accent
        ),
        "subtitle": ParagraphStyle(
            "subtitle",
            parent=base["Normal"],
            fontName=regular,
            fontSize=8.5,
            textColor=colors.grey,
            alignment=TA_CENTER,
        ),
        "h2": ParagraphStyle(
            "h2",
            parent=base["Heading2"],
            fontName=bold,
            fontSize=14,
            textColor=accent,
            spaceBefore=10,
            spaceAfter=4,
        ),
        "h3": ParagraphStyle(
            "h3", parent=base["Heading3"], fontName=bold, fontSize=11.5, spaceBefore=6, spaceAfter=2
        ),
        "body": ParagraphStyle(
            "body", parent=base["Normal"], fontName=regular, fontSize=10, leading=14
        ),
        "explanation": ParagraphStyle(
            "explanation",
            parent=base["Normal"],
            fontName=regular,
            fontSize=10,
            leading=14,
            spaceAfter=4,
        ),
        "point": ParagraphStyle(
            "point",
            parent=base["Normal"],
            fontName=regular,
            fontSize=10,
            leading=14,
            leftIndent=7 * mm,
            bulletIndent=1.5 * mm,
            spaceAfter=2,
        ),
        "formula": ParagraphStyle(
            "formula",
            parent=base["Normal"],
            fontName=regular,
            fontSize=11.5,
            leading=17,
            alignment=TA_CENTER,
        ),
        "cell": ParagraphStyle(
            "cell", parent=base["Normal"], fontName=regular, fontSize=8.5, leading=11
        ),
        "answer": ParagraphStyle(
            "answer",
            parent=base["Normal"],
            fontName=regular,
            fontSize=9.5,
            leading=13,
            leftIndent=7 * mm,
            textColor=colors.HexColor("#444444"),
            spaceAfter=6,
        ),
        "term": ParagraphStyle(
            "term", parent=base["Normal"], fontName=bold, fontSize=10, leading=14
        ),
    }


def _footer(font: str):
    def draw(canvas, doc):
        canvas.saveState()
        canvas.setFont(font, 8)
        canvas.setFillColor(colors.grey)
        canvas.drawCentredString(A4[0] / 2, 10 * mm, f"Page {doc.page}")
        canvas.restoreState()

    return draw
