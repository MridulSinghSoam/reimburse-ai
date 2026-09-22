"""End-to-end API tests using FastAPI's TestClient (no real server needed)."""
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:  # "with" runs the startup code (creates tables)
        yield c


def upload(client, name="Asha", content=b"\x89PNG fake bill 1", ctype="image/png"):
    return client.post("/api/claims", data={"employee_name": name},
                       files={"file": ("bill.png", content, ctype)})


def test_health(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_homepage_is_served(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "ReimburseAI" in res.text


def test_submit_claim_is_scanned_and_classified(client):
    res = upload(client)
    assert res.status_code == 201
    claim = res.json()
    assert claim["status"] == "pending"
    assert claim["amount"] > 0
    assert claim["category"] in ["Fuel", "Meals", "Telecom", "Travel", "Books & Learning", "Other"]


def test_same_file_twice_is_flagged(client):
    upload(client, content=b"\x89PNG duplicate me")
    second = upload(client, content=b"\x89PNG duplicate me").json()
    assert any("Exact same file" in f for f in second["flags"])


def test_wrong_file_type_rejected(client):
    res = upload(client, content=b"hello", ctype="text/plain")
    assert res.status_code == 415


def test_hr_approve_and_reject(client):
    claim_id = upload(client, content=b"\x89PNG approve me").json()["id"]

    ok = client.post(f"/api/claims/{claim_id}/decision", json={"status": "approved"})
    assert ok.status_code == 200 and ok.json()["status"] == "approved"

    again = client.post(f"/api/claims/{claim_id}/decision", json={"status": "rejected", "comment": "x"})
    assert again.status_code == 409  # can't decide twice

    other = upload(client, content=b"\x89PNG reject me").json()["id"]
    no_reason = client.post(f"/api/claims/{other}/decision", json={"status": "rejected"})
    assert no_reason.status_code == 400  # rejection needs a comment


def test_bill_file_can_be_downloaded(client):
    claim_id = upload(client, content=b"\x89PNG view me").json()["id"]
    res = client.get(f"/api/claims/{claim_id}/bill")
    assert res.status_code == 200
    assert res.content == b"\x89PNG view me"


def test_summary(client):
    data = client.get("/api/summary").json()
    assert {"pending", "flagged_pending", "approved_amount", "approved_by_category"} <= data.keys()


# ---------------- Smart Benefit Rebalancer ----------------

def test_benefits_balance_for_new_employee(client):
    data = client.get("/api/benefits", params={"employee": "Neha"}).json()
    assert data["total_budget"] > 0
    assert {c["category"] for c in data["categories"]} >= {"Fuel", "Telecom", "Meals"}


def test_full_rebalance_flow(client):
    who = "Karan"
    ask = client.post("/api/rebalance", json={
        "employee_name": who, "from_category": "Books & Learning", "to_category": "Telecom",
        "amount": 1000, "reason": "Extra internet bill for work from home",
    })
    assert ask.status_code == 201
    move_id = ask.json()["id"]

    # While pending, the money is held but Telecom limit hasn't grown yet
    cats = {c["category"]: c for c in client.get("/api/benefits", params={"employee": who}).json()["categories"]}
    assert cats["Books & Learning"]["pending_out"] == 1000
    before_total = sum(c["limit"] for c in cats.values())

    # HR approves
    ok = client.post(f"/api/rebalance/{move_id}/decision", json={"status": "approved"})
    assert ok.status_code == 200

    cats = {c["category"]: c for c in client.get("/api/benefits", params={"employee": who}).json()["categories"]}
    assert cats["Telecom"]["limit"] == cats["Telecom"]["default_limit"] + 1000
    assert cats["Books & Learning"]["limit"] == cats["Books & Learning"]["default_limit"] - 1000
    assert sum(c["limit"] for c in cats.values()) == before_total  # total never changes

    # Can't decide twice
    again = client.post(f"/api/rebalance/{move_id}/decision", json={"status": "rejected", "comment": "x"})
    assert again.status_code == 409


def test_cannot_move_more_than_available(client):
    res = client.post("/api/rebalance", json={
        "employee_name": "Priya", "from_category": "Fuel", "to_category": "Meals",
        "amount": 999999, "reason": "Too much",
    })
    assert res.status_code == 400
    assert "at most" in res.json()["detail"]


def test_reject_needs_comment_and_frees_money(client):
    who = "Dev"
    move_id = client.post("/api/rebalance", json={
        "employee_name": who, "from_category": "Fuel", "to_category": "Meals",
        "amount": 3000, "reason": "Team lunches this month",
    }).json()["id"]

    # All Fuel money is now held, so a second request fails
    second = client.post("/api/rebalance", json={
        "employee_name": who, "from_category": "Fuel", "to_category": "Travel",
        "amount": 500, "reason": "Cab rides",
    })
    assert second.status_code == 400

    assert client.post(f"/api/rebalance/{move_id}/decision", json={"status": "rejected"}).status_code == 400
    assert client.post(f"/api/rebalance/{move_id}/decision",
                       json={"status": "rejected", "comment": "Meals limit is enough"}).status_code == 200

    cats = {c["category"]: c for c in client.get("/api/benefits", params={"employee": who}).json()["categories"]}
    assert cats["Fuel"]["available"] == cats["Fuel"]["limit"]  # money is free again


def fuel_bill_bytes() -> bytes:
    """Find file bytes that the fake OCR reads as the Rs 1,850 fuel bill."""
    from app.services.ocr import _mock_extract
    i = 0
    while "Oil" not in _mock_extract(b"fuel-%d" % i)["vendor"]:
        i += 1
    return b"fuel-%d" % i


def test_approval_rechecked_if_money_got_used(client):
    """Employee asks to move all Fuel money, then spends some on a fuel bill before HR approves."""
    who = "Meera"
    move_id = client.post("/api/rebalance", json={
        "employee_name": who, "from_category": "Fuel", "to_category": "Travel",
        "amount": 3000, "reason": "Moving all fuel money to cabs",
    }).json()["id"]

    # Now a Rs 1,850 fuel bill comes in, so only Rs 1,150 of Fuel is really free
    bill = client.post("/api/claims", data={"employee_name": who},
                       files={"file": ("fuel.png", fuel_bill_bytes(), "image/png")}).json()
    assert bill["category"] == "Fuel"

    res = client.post(f"/api/rebalance/{move_id}/decision", json={"status": "approved"})
    assert res.status_code == 409
    assert "can't be approved anymore" in res.json()["detail"]
