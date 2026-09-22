"""
classifier.py - Decides which reimbursement category a bill belongs to.

Uses Azure OpenAI (GPT) to read the vendor + items and pick a category,
with a one-line reason. If Azure OpenAI isn't set up or fails, it falls
back to simple keyword rules so the app never breaks.
"""
import json

from ..config import settings

# These match typical Indian flexi-benefit (FBP) reimbursement heads
CATEGORIES = ["Fuel", "Meals", "Telecom", "Travel", "Books & Learning", "Other"]

KEYWORDS = {
    "Fuel": ["petrol", "diesel", "fuel", "indian oil", "hp petrol", "bharat petroleum", "shell", "cng"],
    "Telecom": ["airtel", "jio", "vodafone", " vi ", "bsnl", "broadband", "internet", "postpaid", "recharge", "act fibernet"],
    "Meals": ["cafe", "coffee", "restaurant", "swiggy", "zomato", "food", "dhaba", "kitchen", "pizza", "meal"],
    "Travel": ["uber", "ola", "rapido", "irctc", "metro", "cab", "taxi", "airlines", "indigo", "trip", "bus"],
    "Books & Learning": ["book", "udemy", "coursera", "course", "kindle", "crossword", "exam fee"],
}

SYSTEM_PROMPT = f"""You classify Indian employee reimbursement bills.
Pick exactly one category from: {", ".join(CATEGORIES)}.
Reply ONLY with JSON like {{"category": "Fuel", "reason": "one short sentence"}}."""


def classify(extracted: dict) -> tuple[str, str]:
    """Return (category, reason)."""
    if settings.use_azure_openai:
        try:
            return _classify_with_openai(extracted)
        except Exception as exc:  # network issue, quota, bad JSON...
            category, reason = _classify_with_keywords(extracted)
            return category, f"{reason} (AI unavailable: {type(exc).__name__})"
    return _classify_with_keywords(extracted)


def _classify_with_openai(extracted: dict) -> tuple[str, str]:
    from openai import AzureOpenAI

    client = AzureOpenAI(
        api_key=settings.aoai_key,
        api_version=settings.aoai_api_version,
        azure_endpoint=settings.aoai_endpoint,
    )
    bill_text = (
        f"Vendor: {extracted.get('vendor')}\n"
        f"Items: {', '.join(extracted.get('items') or [])}\n"
        f"Amount: {extracted.get('total')}\n"
        f"Text on bill: {(extracted.get('raw_text') or '')[:1200]}"
    )
    response = client.chat.completions.create(
        model=settings.aoai_deployment,  # this is your *deployment* name in Azure
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": bill_text},
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )
    data = json.loads(response.choices[0].message.content)
    category = data.get("category", "Other")
    if category not in CATEGORIES:  # never trust the model blindly
        category = "Other"
    return category, data.get("reason", "Classified by AI")


def _classify_with_keywords(extracted: dict) -> tuple[str, str]:
    text = " " + " ".join(
        [extracted.get("vendor") or "", *(extracted.get("items") or []), extracted.get("raw_text") or ""]
    ).lower() + " "
    for category, words in KEYWORDS.items():
        for word in words:
            if word in text:
                return category, f'Matched keyword "{word.strip()}"'
    return "Other", "No known keyword found"
