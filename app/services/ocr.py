"""
ocr.py - Reads a bill image/PDF and pulls out vendor, date, total and items.

Uses Azure AI Document Intelligence's ready-made "prebuilt-receipt" model,
which already knows what receipts look like (no training needed).

If Azure keys are not set (e.g. in tests or on your laptop without Azure),
it returns realistic fake data so the rest of the app still works.
"""
import hashlib
import io
from datetime import date

from ..config import settings


def extract_receipt(data: bytes) -> dict:
    """Return a dict: vendor, date (YYYY-MM-DD), total, currency, items, confidence, raw_text."""
    if not settings.use_azure_ocr:
        return _mock_extract(data)

    # Imported here so the app can start even if the SDK isn't configured
    from azure.ai.documentintelligence import DocumentIntelligenceClient
    from azure.core.credentials import AzureKeyCredential

    client = DocumentIntelligenceClient(
        endpoint=settings.docintel_endpoint,
        credential=AzureKeyCredential(settings.docintel_key),
    )
    poller = client.begin_analyze_document("prebuilt-receipt", body=io.BytesIO(data))
    result = poller.result()

    if not result.documents:
        # Azure couldn't find a receipt in the file
        return {"vendor": None, "date": None, "total": None, "currency": "INR",
                "items": [], "confidence": 0.0, "raw_text": result.content or ""}

    doc = result.documents[0]
    fields = doc.fields or {}

    total_field = fields.get("Total")
    total, currency = None, "INR"
    if total_field is not None:
        cur = getattr(total_field, "value_currency", None)
        if cur is not None:
            total = cur.amount
            currency = getattr(cur, "currency_code", None) or "INR"
        elif getattr(total_field, "value_number", None) is not None:
            total = total_field.value_number

    date_field = fields.get("TransactionDate")
    bill_date = None
    if date_field is not None and getattr(date_field, "value_date", None):
        bill_date = date_field.value_date.isoformat()

    vendor_field = fields.get("MerchantName")
    vendor = None
    if vendor_field is not None:
        vendor = getattr(vendor_field, "value_string", None) or vendor_field.content

    items = []
    items_field = fields.get("Items")
    if items_field is not None and getattr(items_field, "value_array", None):
        for item in items_field.value_array:
            obj = getattr(item, "value_object", None) or {}
            desc = obj.get("Description")
            if desc is not None:
                items.append(getattr(desc, "value_string", None) or desc.content)

    return {
        "vendor": vendor,
        "date": bill_date,
        "total": total,
        "currency": currency,
        "items": [i for i in items if i][:15],
        "confidence": doc.confidence,
        "raw_text": (result.content or "")[:2000],
    }


# ---------- Fake data for local/testing ----------
_SAMPLES = [
    {"vendor": "Indian Oil Fuel Station", "total": 1850.0, "items": ["Petrol 17.2 L"]},
    {"vendor": "Airtel Postpaid", "total": 799.0, "items": ["Monthly plan", "GST"]},
    {"vendor": "Cafe Coffee Day", "total": 420.0, "items": ["Cappuccino", "Veg sandwich"]},
    {"vendor": "Uber India", "total": 356.0, "items": ["Trip: Sector 62 to Office"]},
]


def _mock_extract(data: bytes) -> dict:
    # Same file -> same fake result (so duplicate checks still make sense)
    idx = int(hashlib.sha256(data).hexdigest()[:8], 16) % len(_SAMPLES)
    sample = _SAMPLES[idx]
    return {
        "vendor": sample["vendor"],
        "date": date.today().isoformat(),
        "total": sample["total"],
        "currency": "INR",
        "items": sample["items"],
        "confidence": 0.93,
        "raw_text": f"{sample['vendor']} " + " ".join(sample["items"]),
    }
