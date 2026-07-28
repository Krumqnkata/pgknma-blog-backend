from collections import defaultdict
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from django.conf import settings
from django.utils import timezone

from .models import normalize_class_code


class ProfilePdfError(Exception):
    pass


CLASS_LETTER_ORDER = {"A": 0, "B": 1, "V": 2, "G": 3}
CLASS_LETTER_LABELS = {"A": "А", "B": "Б", "V": "В", "G": "Г"}


def _class_sort_key(class_code):
    class_code = normalize_class_code(class_code)
    if class_code == "U":
        return (2, 0, 0)
    try:
        grade = int(class_code[:-1])
        letter_order = CLASS_LETTER_ORDER[class_code[-1]]
    except (KeyError, TypeError, ValueError):
        return (1, class_code, 0)
    return (0, grade, letter_order)


def _class_page_label(class_code):
    class_code = normalize_class_code(class_code)
    if class_code == "U":
        return "Учители"
    if (
        len(class_code) >= 2
        and class_code[-1] in CLASS_LETTER_LABELS
    ):
        return (
            f"{class_code[:-1]}"
            f"{CLASS_LETTER_LABELS[class_code[-1]]} клас"
        )
    return class_code or "Без клас"


def group_profiles_by_class(profiles):
    grouped = defaultdict(list)
    for profile in profiles:
        grouped[normalize_class_code(profile.clas)].append(profile)
    return [
        (
            class_code,
            sorted(
                grouped[class_code],
                key=lambda profile: (
                    profile.first_name.casefold(),
                    profile.last_name.casefold(),
                ),
            ),
        )
        for class_code in sorted(grouped, key=_class_sort_key)
    ]


def _find_font_pair():
    base_dir = Path(settings.BASE_DIR)
    candidates = (
        (
            base_dir / "static" / "fonts" / "DejaVuSans.ttf",
            base_dir / "static" / "fonts" / "DejaVuSans-Bold.ttf",
        ),
        (
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ),
        (
            Path("/usr/share/fonts/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"),
        ),
        (
            Path("C:/Windows/Fonts/arial.ttf"),
            Path("C:/Windows/Fonts/arialbd.ttf"),
        ),
        (
            Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
            Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
        ),
    )
    for regular, bold in candidates:
        if regular.is_file():
            return regular, bold if bold.is_file() else regular
    raise ProfilePdfError(
        "Не е намерен шрифт с кирилица. Инсталирай DejaVu Sans "
        "(пакет fonts-dejavu-core)."
    )


def _register_fonts():
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
    except ImportError as exc:
        raise ProfilePdfError(
            "Липсва библиотеката ReportLab. Инсталирай requirements.txt."
        ) from exc

    regular_path, bold_path = _find_font_pair()
    regular_name = "ValentineProfileRegular"
    bold_name = "ValentineProfileBold"
    registered = set(pdfmetrics.getRegisteredFontNames())
    if regular_name not in registered:
        pdfmetrics.registerFont(TTFont(regular_name, str(regular_path)))
    if bold_name not in registered:
        pdfmetrics.registerFont(TTFont(bold_name, str(bold_path)))
    return regular_name, bold_name


def build_profiles_pdf(profiles, title="Кодове за Valentine платформата"):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    regular_font, bold_font = _register_fonts()
    profiles = list(profiles)
    profile_groups = group_profiles_by_class(profiles)
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=12 * mm,
        leftMargin=12 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title=title,
        author="Valentine платформа",
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ValentineTitle",
        parent=styles["Title"],
        fontName=bold_font,
        fontSize=16,
        leading=20,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#9f1239"),
    )
    class_title_style = ParagraphStyle(
        "ValentineClassTitle",
        parent=title_style,
        fontSize=20,
        leading=24,
        spaceBefore=2 * mm,
    )
    note_style = ParagraphStyle(
        "ValentineNote",
        parent=styles["BodyText"],
        fontName=regular_font,
        fontSize=8,
        leading=11,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#475569"),
    )
    cell_style = ParagraphStyle(
        "ValentineCell",
        parent=styles["BodyText"],
        fontName=regular_font,
        fontSize=8,
        leading=10,
    )
    code_style = ParagraphStyle(
        "ValentineCode",
        parent=cell_style,
        fontName=bold_font,
        fontSize=10,
        alignment=TA_CENTER,
    )

    generated_at = timezone.localtime(timezone.now())
    story = []
    if not profile_groups:
        story.extend(
            [
                Paragraph(escape(title), title_style),
                Spacer(1, 5 * mm),
                Paragraph("Няма профили за експортиране.", note_style),
            ]
        )

    for group_index, (class_code, class_profiles) in enumerate(
        profile_groups
    ):
        if group_index:
            story.append(PageBreak())
        class_label = _class_page_label(class_code)
        story.extend(
            [
                Paragraph(escape(title), title_style),
                Paragraph(escape(class_label), class_title_style),
                Spacer(1, 2 * mm),
                Paragraph(
                    "Поверителен документ. Раздавай всеки код само на "
                    "съответния човек.",
                    note_style,
                ),
                Paragraph(
                    f"Генериран на {generated_at:%d.%m.%Y %H:%M} · "
                    f"Профили в секцията: {len(class_profiles)}",
                    note_style,
                ),
                Spacer(1, 5 * mm),
            ]
        )

        data = [
            [
                "#",
                "Име",
                "Клас",
                "Учебна година",
                "Код",
                "Статус",
            ]
        ]
        for index, profile in enumerate(class_profiles, start=1):
            data.append(
                [
                    str(index),
                    Paragraph(escape(profile.full_name), cell_style),
                    Paragraph(escape(profile.clas_bg), cell_style),
                    Paragraph(
                        escape(profile.academic_year_label),
                        cell_style,
                    ),
                    Paragraph(escape(profile.code), code_style),
                    Paragraph(
                        "Активен" if profile.is_active else "Неактивен",
                        cell_style,
                    ),
                ]
            )

        table = Table(
            data,
            repeatRows=1,
            colWidths=(
                9 * mm,
                67 * mm,
                20 * mm,
                32 * mm,
                27 * mm,
                25 * mm,
            ),
            hAlign="CENTER",
        )
        table.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, 0), bold_font),
                    ("FONTSIZE", (0, 0), (-1, 0), 8),
                    (
                        "BACKGROUND",
                        (0, 0),
                        (-1, 0),
                        colors.HexColor("#ffe4e6"),
                    ),
                    (
                        "TEXTCOLOR",
                        (0, 0),
                        (-1, 0),
                        colors.HexColor("#881337"),
                    ),
                    ("ALIGN", (0, 0), (0, -1), "CENTER"),
                    ("ALIGN", (2, 1), (-1, -1), "CENTER"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    (
                        "GRID",
                        (0, 0),
                        (-1, -1),
                        0.3,
                        colors.HexColor("#cbd5e1"),
                    ),
                    (
                        "ROWBACKGROUNDS",
                        (0, 1),
                        (-1, -1),
                        (
                            colors.white,
                            colors.HexColor("#fff7f8"),
                        ),
                    ),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        story.append(table)

    def add_page_number(canvas, doc):
        canvas.saveState()
        canvas.setFont(regular_font, 8)
        canvas.setFillColor(colors.HexColor("#64748b"))
        canvas.drawCentredString(
            A4[0] / 2,
            7 * mm,
            f"Страница {doc.page}",
        )
        canvas.restoreState()

    document.build(
        story,
        onFirstPage=add_page_number,
        onLaterPages=add_page_number,
    )
    return buffer.getvalue()
