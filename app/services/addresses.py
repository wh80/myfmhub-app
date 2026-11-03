import re

# Locations and suppliers have one address, stored as lines separated by \n. Forms take it from a textarea,
# one line per row; imports take it from one CSV cell, where commas separate the lines

MAX_ADDRESS_LENGTH = 500


def clean_address(value, commas=False):
    """The address with each line trimmed and blank lines dropped, or None when nothing is left.
    commas=True (imports) also splits lines on commas, which are dropped."""
    if not value:
        return None
    separators = r'[\r\n,]+' if commas else r'[\r\n]+'
    lines = [line.strip() for line in re.split(separators, value)]
    return '\n'.join(line for line in lines if line) or None
