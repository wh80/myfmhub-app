import csv
import io
import re
from datetime import date, datetime
from decimal import Decimal
from xml.sax.saxutils import escape

from flask import Response, request
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.services.scheduling import UK_TIMEZONE, uk_today

# downloads of an index page. A column is (key, label, getter), in table order. The key matches
# data-col on the table header, so app.js can pass the ticked columns as ?columns=key,key

# a PDF this long is slow to build and no use to read. The CSV has no limit
PDF_MAX_ROWS = 2000

# Excel runs a cell starting with one of these as a formula, so a value like =HYPERLINK(...) typed
# into a record could run for whoever opens the CSV. A leading ' makes Excel treat it as text.
# Numbers and phone numbers such as +44 20 7946 0000 are left alone
FORMULA_START = ('=', '+', '-', '@', '\t', '\r')
NUMBER_LIKE = re.compile(r'^[+-]?[\d\s().]+$')


def selected_columns(columns):
    """The columns named in ?columns=, in table order. All of them if it's missing or names none."""
    keys = set(request.args.get('columns', '').split(','))
    return [column for column in columns if column[0] in keys] or columns


def csv_value(value):
    if value is None:
        return ''
    if isinstance(value, bool):
        return 'Yes' if value else 'No'
    if isinstance(value, datetime):
        return value.strftime('%d-%m-%Y %H:%M')
    if isinstance(value, date):
        return value.strftime('%d-%m-%Y')
    if isinstance(value, Decimal):
        return f'{value:.2f}'
    value = str(value)
    if value.startswith(FORMULA_START) and not NUMBER_LIKE.match(value):
        return "'" + value
    return value


def pdf_value(value):
    if value is None:
        return '-'
    if isinstance(value, Decimal):
        return f'£{value:,.2f}'
    return csv_value(value).removeprefix("'")


def attachment(name, extension):
    return {'Content-Disposition': f'attachment; filename="{name}-{uk_today().isoformat()}.{extension}"'}


def csv_response(name, columns, items):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([label for _, label, _ in columns])
    for item in items:
        writer.writerow([csv_value(getter(item)) for _, _, getter in columns])
    # the BOM makes Excel read the file as UTF-8
    return Response('﻿' + output.getvalue(), mimetype='text/csv', headers=attachment(name, 'csv'))


def page_number(canvas, doc):
    canvas.saveState()
    canvas.setFont('Helvetica', 8)
    canvas.setFillColor(colors.grey)
    canvas.drawRightString(doc.pagesize[0] - doc.rightMargin, 8 * mm, f'Page {doc.page}')
    canvas.restoreState()


def pdf_response(name, title, columns, items):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=landscape(A4), title=title,
        leftMargin=12 * mm, rightMargin=12 * mm, topMargin=12 * mm, bottomMargin=15 * mm,
    )
    cell = ParagraphStyle('cell', fontName='Helvetica', fontSize=8, leading=10)
    header = ParagraphStyle('header', parent=cell, fontName='Helvetica-Bold')
    heading = ParagraphStyle('heading', fontName='Helvetica-Bold', fontSize=14, leading=18)
    note = ParagraphStyle('note', parent=cell, textColor=colors.grey)

    total = len(items)
    summary = f"{total} record(s). Generated {datetime.now(UK_TIMEZONE):%d-%m-%Y %H:%M}."
    if total > PDF_MAX_ROWS:
        summary += f' Showing the first {PDF_MAX_ROWS}. Download the CSV for all of them.'

    rows = [[Paragraph(escape(label), header) for _, label, _ in columns]]
    rows += [[Paragraph(escape(pdf_value(getter(item))), cell) for _, _, getter in columns] for item in items[:PDF_MAX_ROWS]]
    table = Table(rows, colWidths=[doc.width / len(columns)] * len(columns), repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e9ecef')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
        ('LINEBELOW', (0, 0), (-1, -1), 0.25, colors.HexColor('#dee2e6')),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))

    doc.build(
        [Paragraph(escape(title), heading), Paragraph(escape(summary), note), Spacer(1, 4 * mm), table],
        onFirstPage=page_number, onLaterPages=page_number,
    )
    return Response(buffer.getvalue(), mimetype='application/pdf', headers=attachment(name, 'pdf'))


def export_response(file_format, name, title, columns, items):
    columns = selected_columns(columns)
    if file_format == 'csv':
        return csv_response(name, columns, items)
    return pdf_response(name, title, columns, items)
