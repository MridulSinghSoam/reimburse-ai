"""Unit tests for the policy / fraud rules."""
from datetime import date

from app.services.rules import check_flags

TODAY = date(2026, 9, 21)


def bill(**overrides):
    base = {"vendor": "Indian Oil", "date": "2026-09-18", "total": 1850.0, "confidence": 0.95}
    base.update(overrides)
    return base


def test_clean_bill_has_no_flags():
    assert check_flags(bill(), "Fuel", "Asha", "hash1", [], TODAY) == []


def test_future_date_is_flagged():
    flags = check_flags(bill(date="2026-10-01"), "Fuel", "Asha", "h", [], TODAY)
    assert "Bill date is in the future" in flags


def test_old_bill_is_flagged():
    flags = check_flags(bill(date="2026-01-01"), "Fuel", "Asha", "h", [], TODAY)
    assert any("older than" in f for f in flags)


def test_missing_total_is_flagged():
    flags = check_flags(bill(total=None), "Fuel", "Asha", "h", [], TODAY)
    assert "Total amount could not be read" in flags


def test_exact_duplicate_file_is_flagged():
    earlier = [{"id": 7, "file_hash": "same", "employee_name": "Asha", "vendor": "Indian Oil",
                "amount": 1850.0, "bill_date": "2026-09-18", "category": "Fuel", "status": "pending"}]
    flags = check_flags(bill(), "Fuel", "Asha", "same", earlier, TODAY)
    assert "Exact same file as claim #7" in flags


def test_same_details_different_photo_is_flagged():
    earlier = [{"id": 3, "file_hash": "other", "employee_name": "Asha", "vendor": "indian oil",
                "amount": 1850.0, "bill_date": "2026-09-18", "category": "Fuel", "status": "approved"}]
    flags = check_flags(bill(), "Fuel", "Asha", "new", earlier, TODAY)
    assert any("duplicate of claim #3" in f for f in flags)


def test_monthly_limit_is_enforced():
    earlier = [{"id": 1, "file_hash": "a", "employee_name": "Asha", "vendor": "HP", "amount": 2000.0,
                "bill_date": "2026-09-02", "category": "Fuel", "status": "approved"}]
    flags = check_flags(bill(), "Fuel", "Asha", "b", earlier, TODAY)
    assert any("monthly Fuel limit" in f for f in flags)


def test_rejected_claims_do_not_count_towards_limit():
    earlier = [{"id": 1, "file_hash": "a", "employee_name": "Asha", "vendor": "HP", "amount": 2000.0,
                "bill_date": "2026-09-02", "category": "Fuel", "status": "rejected"}]
    assert check_flags(bill(), "Fuel", "Asha", "b", earlier, TODAY) == []


def test_other_category_not_covered():
    flags = check_flags(bill(), "Other", "Asha", "h", [], TODAY)
    assert any("not covered" in f for f in flags)


def test_round_amount_is_flagged():
    flags = check_flags(bill(total=2500.0), "Travel", "Asha", "h", [], TODAY)
    assert "Amount is a suspiciously round number" in flags
