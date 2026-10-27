import calendar
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# customers are in the UK, so "today" means the UK date whatever the server's timezone,
# and whether the UK is on GMT or BST
UK_TIMEZONE = ZoneInfo('Europe/London')

# how often a job schedule repeats. The key is stored on JobSchedule.frequency
FREQUENCIES = [
    ('daily', 'Every Day'),
    ('weekly', 'Every Week'),
    ('six_weekly', 'Every 6 Weeks'),
    ('monthly', 'Every Month'),
    ('three_monthly', 'Every 3 Months'),
    ('six_monthly', 'Every 6 Months'),
    ('yearly', 'Every Year'),
    ('two_yearly', 'Every 2 Years'),
    ('three_yearly', 'Every 3 Years'),
    ('five_yearly', 'Every 5 Years'),
]
FREQUENCY_LABELS = dict(FREQUENCIES)

# the shortest possible gap between two jobs, in days. Months and years use their
# shortest calendar span, e.g. 28 days for a month starting in February
FREQUENCY_MIN_DAYS = {
    'daily': 1,
    'weekly': 7,
    'six_weekly': 42,
    'monthly': 28,
    'three_monthly': 89,
    'six_monthly': 181,
    'yearly': 365,
    'two_yearly': 730,
    'three_yearly': 1095,
    'five_yearly': 1826,
}


# the job is raised notice_days before it's due. This can be in the past when the due date is close
def job_creation_date(next_due, notice_days):
    return next_due - timedelta(days=notice_days)


# how far each frequency moves next_due on. Months and years are calendar steps, not a fixed number of days
FREQUENCY_DAYS = {
    'daily': 1,
    'weekly': 7,
    'six_weekly': 42,
}
FREQUENCY_MONTHS = {
    'monthly': 1,
    'three_monthly': 3,
    'six_monthly': 6,
    'yearly': 12,
    'two_yearly': 24,
    'three_yearly': 36,
    'five_yearly': 60,
}


def uk_today():
    return datetime.now(UK_TIMEZONE).date()


# the UK date of a stored timestamp. created_at and updated_at are naive UTC
def uk_date(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(UK_TIMEZONE).date()


# the 31st becomes the last day of a shorter month, e.g. 31-01 + 1 month is 28-02 (29-02 in a leap year)
def add_months(value, months):
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def next_due_date(due, frequency):
    if frequency in FREQUENCY_DAYS:
        return due + timedelta(days=FREQUENCY_DAYS[frequency])
    return add_months(due, FREQUENCY_MONTHS[frequency])
