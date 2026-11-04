import io
import zipfile
from datetime import date, datetime, time
from decimal import Decimal

from openpyxl import Workbook, load_workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

# Excel (.xlsx) files: reading an uploaded import's first sheet as text cells, writing an import template
# with a dropdown on the columns that have a list of choices, and writing downloads. openpyxl parses the
# workbook's XML through defusedxml (in requirements.txt), which refuses XML entity tricks

EXCEL_MIMETYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

# a .xlsx is a zip of XML. Refuse one that unzips to more than this, so a small file can't expand to fill
# the server's memory (a "zip bomb")
MAX_UNZIPPED_BYTES = 50 * 1024 * 1024
# a header row wider than this isn't an import file
MAX_COLUMNS = 200
# the rows the template's dropdowns and formats cover, under the header
TEMPLATE_ROWS = 5000
LISTS_SHEET = 'Lists'
# formats for downloads, matching how pages show dates
DATE_FORMAT = 'dd-mm-yyyy'
DATETIME_FORMAT = 'dd-mm-yyyy hh:mm'
NUMBER_FORMAT = '0.00'
MAX_COLUMN_WIDTH = 60


class SpreadsheetError(Exception):
    pass


def cell_text(value):
    """An Excel cell's value as text, so every import column is parsed and checked the same way."""
    if value is None:
        return ''
    if isinstance(value, bool):
        return 'Yes' if value else 'No'
    if isinstance(value, datetime):
        # a date typed in Excel comes back as a datetime at midnight
        if value.time() == time(0, 0):
            return value.date().isoformat()
        return value.strftime('%Y-%m-%d %H:%M')
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        # repr is the shortest exact form (1250.5, not 1250.49999...), and 'f' avoids 1e-05
        return format(Decimal(repr(value)), 'f')
    return str(value)


def read_xlsx(data):
    """The first sheet's rows, each a list of text cells. Trailing blank header cells are dropped (Excel
    files often have formatted but empty cells after the last column). Raises SpreadsheetError."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if sum(info.file_size for info in archive.infolist()) > MAX_UNZIPPED_BYTES:
                raise SpreadsheetError('The file is too big once unzipped. Split it into smaller files.')
    except zipfile.BadZipFile:
        # an old .xls, a password-protected workbook or something renamed .xlsx
        raise SpreadsheetError("The file couldn't be read as an Excel workbook. Save it as an Excel Workbook (.xlsx) "
                               "without a password.")

    try:
        # data_only: a formula cell gives the value Excel last calculated, never the formula
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:
        raise SpreadsheetError("The file couldn't be read as an Excel workbook. Save it as an Excel Workbook (.xlsx).")

    try:
        if not workbook.worksheets:
            raise SpreadsheetError('The workbook has no sheets.')
        sheet = workbook.worksheets[0]
        # the stored size can be missing or wrong, so read until the rows run out
        sheet.reset_dimensions()
        rows = sheet.iter_rows(values_only=True)
        try:
            header = [cell_text(value).strip() for value in next(rows)]
        except StopIteration:
            return []
        while header and header[-1] == '':
            header.pop()
        if len(header) > MAX_COLUMNS:
            raise SpreadsheetError(f'The first sheet has more than {MAX_COLUMNS} columns.')
        result = [header]
        for cells in rows:
            result.append([cell_text(value) for value in cells[:len(header)]])
        return result
    except SpreadsheetError:
        raise
    except Exception:
        raise SpreadsheetError("The file couldn't be read as an Excel workbook. Save it as an Excel Workbook (.xlsx).")
    finally:
        workbook.close()


def set_text(cell, value):
    """Store value as text, even if it starts with = (openpyxl would otherwise write it as a formula)."""
    cell.value = value
    cell.data_type = 's'


def template_xlsx(sheet_title, columns):
    """A workbook with one sheet to fill in. columns is a list of dicts:
    key (the header), comment (shown on the header cell), width, number_format (Excel format code, '@' for
    text, so 00123 keeps its zeros), choices (a list of values for a dropdown, or None) and strict
    (True refuses anything not in the list; False only warns, for lists of your records, which can be out of
    date by the time the file is imported). The choices go on a hidden Lists sheet."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = sheet_title[:31]
    lists = workbook.create_sheet(LISTS_SHEET)
    lists.sheet_state = 'hidden'
    last_row = TEMPLATE_ROWS + 1
    list_column = 0

    for number, column in enumerate(columns, start=1):
        letter = get_column_letter(number)
        header = sheet.cell(row=1, column=number)
        set_text(header, column['key'])
        header.font = Font(bold=True)
        header.number_format = '@'
        if column.get('comment'):
            comment = Comment(column['comment'], 'MyFmHub')
            comment.width, comment.height = 320, 160
            header.comment = comment
        sheet.column_dimensions[letter].width = column.get('width', 20)
        # the format for cells typed into the column (a column style; it needs no cells written)
        sheet.column_dimensions[letter].number_format = column.get('number_format', 'General')

        choices = column.get('choices')
        if not choices:
            continue
        list_column += 1
        list_letter = get_column_letter(list_column)
        set_text(lists.cell(row=1, column=list_column), column['key'])
        for row, choice in enumerate(choices, start=2):
            set_text(lists.cell(row=row, column=list_column), choice)
        strict = column.get('strict', False)
        validation = DataValidation(
            type='list',
            formula1=f"'{LISTS_SHEET}'!${list_letter}$2:${list_letter}${len(choices) + 1}",
            allow_blank=True,
            showErrorMessage=True,
            errorStyle='stop' if strict else 'warning',
            errorTitle=column['key'],
            error=('Choose one from the list.' if strict else
                   "That isn't in the list from when the template was downloaded. Keep it only if it's been added since."),
        )
        validation.add(f'{letter}2:{letter}{last_row}')
        sheet.add_data_validation(validation)

    sheet.freeze_panes = 'A2'
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def export_cell(sheet, value):
    """A cell for a download. Dates and numbers are real ones, so Excel can sort and add them up. Everything
    else is text, so a value like =HYPERLINK(...) typed into a record can never run as a formula."""
    cell = WriteOnlyCell(sheet)
    if value is None:
        return cell
    if isinstance(value, bool):
        cell.value = 'Yes' if value else 'No'
    elif isinstance(value, datetime):
        # Excel has no time zones (openpyxl refuses them), so this keeps the time as it reads
        cell.value = value.replace(tzinfo=None)
        cell.number_format = DATETIME_FORMAT
    elif isinstance(value, date):
        cell.value = value
        cell.number_format = DATE_FORMAT
    elif isinstance(value, Decimal):
        cell.value = value
        cell.number_format = NUMBER_FORMAT
    elif isinstance(value, (int, float)):
        cell.value = value
    else:
        # openpyxl refuses control characters, which can arrive in pasted text
        text = ILLEGAL_CHARACTERS_RE.sub('', str(value))
        set_text(cell, text)
        if '\n' in text:
            cell.alignment = Alignment(wrap_text=True, vertical='top')
    return cell


def column_width(header, values):
    longest = len(header)
    for value in values:
        if isinstance(value, datetime):
            longest = max(longest, 16)
        elif isinstance(value, date):
            longest = max(longest, 10)
        elif value is not None:
            longest = max(longest, *(len(line) for line in str(value).split('\n')))
    return min(longest + 3, MAX_COLUMN_WIDTH)


def export_xlsx(sheets):
    """A workbook's bytes for a download. sheets is a list of (title, headers, rows), where rows is a list of
    lists of values. Write-only mode, so a big export doesn't hold every cell object in memory."""
    workbook = Workbook(write_only=True)
    for title, headers, rows in sheets:
        sheet = workbook.create_sheet(title[:31])
        sheet.freeze_panes = 'A2'
        # with the widths, these have to be set before the first row is added
        for number, header in enumerate(headers, start=1):
            sheet.column_dimensions[get_column_letter(number)].width = column_width(header, [row[number - 1] for row in rows])
        if headers:
            sheet.auto_filter.ref = f'A1:{get_column_letter(len(headers))}{len(rows) + 1}'
        header_cells = []
        for header in headers:
            cell = WriteOnlyCell(sheet)
            set_text(cell, header)
            cell.font = Font(bold=True)
            header_cells.append(cell)
        sheet.append(header_cells)
        for row in rows:
            sheet.append([export_cell(sheet, value) for value in row])
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()
