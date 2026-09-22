"""
benefits.py - Smart Benefit Rebalancer (plain Python, no Azure, easy to test).

The user problem (from Prosperr Benefits App Store feedback):
  "I should be able to decide how to use my flexi benefits, e.g. when an
   unplanned expense comes up mid-month."

How we solve it:
  - Each employee has a fixed TOTAL monthly flexi budget split into categories.
  - They can ask to move UNUSED money from one category to another.
  - HR approves. The TOTAL never changes, so payroll/tax totals stay the same.

A person's limit for a category in a month is:
    default limit + approved moves INTO it - approved moves OUT of it
"""
from datetime import date

from .rules import MONTHLY_LIMITS

# "Other" has limit 0 (not covered), so it can't take part in moves
REBALANCE_CATEGORIES = [c for c, limit in MONTHLY_LIMITS.items() if limit > 0]
MIN_MOVE = 100  # smallest move allowed, in rupees


def current_month(today: date | None = None) -> str:
    return (today or date.today()).isoformat()[:7]  # "YYYY-MM"


def _mine(items, employee, month):
    return [i for i in items if i["employee_name"] == employee and i["month"] == month]


def effective_limits(employee: str, month: str, moves: list[dict]) -> dict:
    """Default limits + this employee's APPROVED moves for this month."""
    limits = dict(MONTHLY_LIMITS)
    for m in _mine(moves, employee, month):
        if m["status"] == "approved":
            limits[m["from_category"]] -= m["amount"]
            limits[m["to_category"]] += m["amount"]
    return limits


def used_by_category(employee: str, month: str, claims: list[dict]) -> dict:
    """Money already claimed this month (pending + approved bills; rejected don't count)."""
    used = {c: 0.0 for c in MONTHLY_LIMITS}
    for c in claims:
        if (c["employee_name"] == employee and c["status"] != "rejected"
                and (c.get("bill_date") or "")[:7] == month and c.get("amount")):
            used[c["category"]] = used.get(c["category"], 0.0) + c["amount"]
    return used


def balance(employee: str, month: str, claims: list[dict], moves: list[dict],
            ignore_move_id: int | None = None) -> dict[str, dict]:
    """
    For each category: limit, used, money waiting to move in/out, and available.
    'available' = what can still be claimed OR moved away right now.
    ignore_move_id lets HR re-check a pending request without counting it twice.
    """
    limits = effective_limits(employee, month, moves)
    used = used_by_category(employee, month, claims)
    pending_out = {c: 0.0 for c in MONTHLY_LIMITS}
    pending_in = {c: 0.0 for c in MONTHLY_LIMITS}
    for m in _mine(moves, employee, month):
        if m["status"] == "pending" and m["id"] != ignore_move_id:
            pending_out[m["from_category"]] += m["amount"]
            pending_in[m["to_category"]] += m["amount"]

    result = {}
    for cat in REBALANCE_CATEGORIES:
        available = limits[cat] - used[cat] - pending_out[cat]
        result[cat] = {
            "category": cat,
            "default_limit": MONTHLY_LIMITS[cat],
            "limit": round(limits[cat], 2),
            "used": round(used[cat], 2),
            "pending_out": round(pending_out[cat], 2),
            "pending_in": round(pending_in[cat], 2),
            "available": round(max(0.0, available), 2),
        }
    return result


def validate_move(employee: str, month: str, from_cat: str, to_cat: str, amount: float,
                  claims: list[dict], moves: list[dict],
                  ignore_move_id: int | None = None) -> str | None:
    """Return an error message, or None if the move is allowed."""
    if from_cat not in REBALANCE_CATEGORIES or to_cat not in REBALANCE_CATEGORIES:
        return "Choose two categories that are part of your flexi plan."
    if from_cat == to_cat:
        return "Choose two different categories."
    if amount < MIN_MOVE:
        return f"Move at least Rs {MIN_MOVE}."
    available = balance(employee, month, claims, moves, ignore_move_id)[from_cat]["available"]
    if amount > available:
        return (f"You can move at most Rs {available:,.0f} from {from_cat} right now, "
                "because the rest is already used or waiting to move.")
    return None


def total_budget() -> float:
    """The total monthly flexi budget. Moves never change this."""
    return float(sum(MONTHLY_LIMITS[c] for c in REBALANCE_CATEGORIES))
