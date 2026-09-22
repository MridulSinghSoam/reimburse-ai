"""Unit tests for the Smart Benefit Rebalancer logic."""
from app.services import benefits
from app.services.rules import MONTHLY_LIMITS

M = "2026-09"


def claim(cat, amount, status="pending", who="Asha", day="2026-09-10"):
    return {"employee_name": who, "category": cat, "amount": amount, "status": status, "bill_date": day}


def move(id, frm, to, amount, status="pending", who="Asha", month=M):
    return {"id": id, "employee_name": who, "month": month, "from_category": frm,
            "to_category": to, "amount": amount, "status": status}


def test_default_balance_matches_policy():
    bal = benefits.balance("Asha", M, [], [])
    assert bal["Fuel"]["limit"] == MONTHLY_LIMITS["Fuel"]
    assert bal["Fuel"]["available"] == MONTHLY_LIMITS["Fuel"]
    assert "Other" not in bal  # not part of the flexi plan


def test_used_money_reduces_available():
    bal = benefits.balance("Asha", M, [claim("Fuel", 1850)], [])
    assert bal["Fuel"]["used"] == 1850
    assert bal["Fuel"]["available"] == MONTHLY_LIMITS["Fuel"] - 1850


def test_rejected_bills_and_other_months_dont_count():
    claims = [claim("Fuel", 1000, status="rejected"), claim("Fuel", 500, day="2026-08-30")]
    assert benefits.balance("Asha", M, claims, [])["Fuel"]["used"] == 0


def test_approved_move_changes_limits_but_not_total():
    moves = [move(1, "Books & Learning", "Telecom", 1000, status="approved")]
    limits = benefits.effective_limits("Asha", M, moves)
    assert limits["Telecom"] == MONTHLY_LIMITS["Telecom"] + 1000
    assert limits["Books & Learning"] == MONTHLY_LIMITS["Books & Learning"] - 1000
    assert sum(limits.values()) == sum(MONTHLY_LIMITS.values())


def test_pending_move_holds_money():
    bal = benefits.balance("Asha", M, [], [move(1, "Fuel", "Meals", 1000)])
    assert bal["Fuel"]["pending_out"] == 1000
    assert bal["Fuel"]["available"] == MONTHLY_LIMITS["Fuel"] - 1000
    assert bal["Meals"]["limit"] == MONTHLY_LIMITS["Meals"]  # not added until approved


def test_cannot_move_more_than_unused():
    err = benefits.validate_move("Asha", M, "Fuel", "Telecom", 2000, [claim("Fuel", 1850)], [])
    assert err and "at most Rs 1,150" in err


def test_cannot_move_same_category_or_other_or_tiny_amounts():
    assert benefits.validate_move("Asha", M, "Fuel", "Fuel", 500, [], [])
    assert benefits.validate_move("Asha", M, "Fuel", "Other", 500, [], [])
    assert benefits.validate_move("Asha", M, "Fuel", "Meals", 50, [], [])


def test_valid_move_passes():
    assert benefits.validate_move("Asha", M, "Books & Learning", "Telecom", 1000, [], []) is None


def test_other_employees_are_separate():
    moves = [move(1, "Fuel", "Meals", 3000, status="approved", who="Ravi")]
    assert benefits.effective_limits("Asha", M, moves)["Fuel"] == MONTHLY_LIMITS["Fuel"]
