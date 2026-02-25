from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.enums import TA_LEFT


def generate_pdf(title, content, output_path):
    """Generate a PDF file from the given title and content."""
    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        rightMargin=2 * cm,
        leftMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "CustomTitle",
        parent=styles["Title"],
        fontSize=24,
        spaceAfter=30,
    )
    body_style = ParagraphStyle(
        "CustomBody",
        parent=styles["Normal"],
        fontSize=12,
        leading=18,
        alignment=TA_LEFT,
        spaceAfter=12,
    )

    story = []
    story.append(Paragraph(title, title_style))
    story.append(Spacer(1, 0.5 * cm))

    for paragraph in content.split("\n"):
        paragraph = paragraph.strip()
        if paragraph:
            story.append(Paragraph(paragraph, body_style))

    doc.build(story)
