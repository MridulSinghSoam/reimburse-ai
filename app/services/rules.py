"""
rules.py - Company reimbursement policy + fraud / mistake checks.

This is plain Python with no Azure calls, so it's easy to unit test.
Each check adds a human-readable "flag" that HR sees before approving.
"""
from datetime import date

# Monthly limits per category in rupees (like a Flexi Benefit Plan)
MONTHLY_LIMITS = {
    "Fuel": 3000,
    "Meals": 2200,
    "Telecom": 1500,
    "Travel": 5000,
    "Books & Learning": 2000,
    "Other": 0,  # not reimbursable without special approval
}
MAX_BILL_AGE_DAYS = 90
LOW_CONFIDENCE = 0.6


def _parse_date(value):
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def check_flags(extracted: dict, category: str, employee_name: str,
                file_hash: str, existing: list[dict], today: date | None = None,
                limits: dict | None = None) -> list[str]:
    """
    extracted: output of ocr.extract_receipt
    existing:  earlier claims (as dicts) from this employee or with the same file
    limits:    this employee's limits for the month (after approved rebalances);
               defaults to the company-wide MONTHLY_LIMITS
    returns:   list of warning messages (empty list = looks clean)
    """
    limits = limits or MONTHLY_LIMITS
    today = today or date.today()
    flags = []

    total = extracted.get("total")
    vendor = (extracted.get("vendor") or "").strip()
    bill_date = _parse_date(extracted.get("date"))

    # 1. Missing information
    if total is None:
        flags.append("Total amount could not be read")
    if not vendor:
        flags.append("Vendor name is missing")
    if bill_date is None:
        flags.append("Bill date is missing")

    # 2. Date problems
    if bill_date and bill_date > today:
        flags.append("Bill date is in the future")
    elif bill_date and (today - bill_date).days > MAX_BILL_AGE_DAYS:
        flags.append(f"Bill is older than {MAX_BILL_AGE_DAYS} days")

    # 3. Poor scan
    confidence = extracted.get("confidence")
    if confidence is not None and confidence < LOW_CONFIDENCE:
        flags.append(f"Low scan confidence ({confidence:.0%}); check the bill manually")

    # 4. Duplicates
    exact = next((c for c in existing if c["file_hash"] == file_hash), None)
    if exact:
        flags.append(f"Exact same file as claim #{exact['id']}")
    else:
        for c in existing:
            if (vendor and (c.get("vendor") or "").lower() == vendor.lower()
                    and c.get("amount") == total and c.get("bill_date") == extracted.get("date")):
                flags.append(f"Looks like a duplicate of claim #{c['id']} (same vendor, amount and date)")
                break

    # 5. Policy limits
    limit = limits.get(category, 0)
    if MONTHLY_LIMITS.get(category, 0) == 0:
        flags.append(f'"{category}" is not covered by the reimbursement policy')
    elif total is not None and bill_date is not None:
        month = bill_date.isoformat()[:7]  # "YYYY-MM"
        already = sum(
            c["amount"] or 0 for c in existing
            if c["employee_name"] == employee_name
            and c["category"] == category
            and c["status"] != "rejected"
            and (c.get("bill_date") or "")[:7] == month
        )
        if already + total > limit:
            flags.append(
                f"Goes over the monthly {category} limit of Rs {limit:,.0f} "
                f"(already claimed Rs {already:,.0f} this month)"
            )

    # 6. Suspiciously round big amounts (common in fake bills)
    if total is not None and total >= 1000 and total % 500 == 0:
        flags.append("Amount is a suspiciously round number")

    return flags

