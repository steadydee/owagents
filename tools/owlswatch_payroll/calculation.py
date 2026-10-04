"""Integer-COP arithmetic and semimonthly calendars, independent of the model."""
import calendar
import datetime as dt
import re

ROUNDING_RULE = "cop-monthly-reconcile-v1"
PERIOD_RE = re.compile(r"^(20\d{2})-(0[1-9]|1[0-2])-H([12])$")
MAX_MONEY = 10 ** 13


def period_parts(value):
    match = PERIOD_RE.fullmatch(str(value))
    if not match:
        raise ValueError("Period must be YYYY-MM-H1 or YYYY-MM-H2.")
    return tuple(int(part) for part in match.groups())


def period_dates(value):
    year, month, half = period_parts(value)
    start = dt.date(year, month, 1 if half == 1 else 16)
    end = dt.date(year, month, 15 if half == 1 else calendar.monthrange(year, month)[1])
    return start.isoformat(), end.isoformat()


def money(value, positive=False):
    if type(value) is not int or value < (1 if positive else 0) or value > MAX_MONEY:
        raise ValueError("Amounts must be whole COP within the supported range.")
    return value


def half_compensation(profile, period):
    """Reconcile both halves to the exact monthly amount, including odd pesos.

    The first half's combined gross is floored once. Salary is floored; the
    allowance takes the remainder. The second half receives monthly residuals.
    This avoids introducing an extra peso by rounding both components upward.
    """
    _, _, half = period_parts(period)
    salary = money(profile["monthly_salary"])
    allowance = money(profile["monthly_allowance"])
    first_salary = salary // 2
    first_allowance = (salary + allowance) // 2 - first_salary
    if half == 1:
        return first_salary, first_allowance
    return salary - first_salary, allowance - first_allowance


def totals(rows):
    return {
        "employees": sum(row["net"] for row in rows if row["kind"] == "employee"),
        "contractors": sum(row["net"] for row in rows if row["kind"] == "contractor"),
        "gross": sum(row["gross"] for row in rows),
        "deductions": sum(row["gross"] - row["net"] for row in rows),
        "loans": sum(sum(item["amount"] for item in row["loan_deductions"]) for row in rows),
        "net": sum(row["net"] for row in rows),
    }
