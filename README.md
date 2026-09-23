# ReimburseAI: AI Reimbursement Bill Scanner

Employees upload a bill (fuel, phone, food, travel). AI reads it, picks the reimbursement
category, checks company policy and fraud rules, and sends it to HR for one-click approval.
With the **Smart Benefit Rebalancer**, employees can also move unused money between
categories when an unplanned expense comes up, without changing their total budget.
Hosted on **Microsoft Azure** with **CI/CD through GitHub Actions**.

Live demo: `https://<your-app-name>.azurewebsites.net`

---

## 1. What problem does this solve?

In Indian companies, salaried employees get tax-free allowances (fuel, telecom, meals, books,
often called a Flexi Benefit Plan). To claim them, employees upload bills every month, and HR
checks each one by hand: Is the amount right? Is it the right category? Was this bill already
claimed? Is it over the monthly limit?

That is slow and error-prone. This app automates it:

| Step | Who / what does it | Azure service |
|---|---|---|
| 1. Employee uploads a bill photo or PDF | Web app | App Service |
| 2. Read vendor, date, total, items | AI OCR | **Azure AI Document Intelligence** (prebuilt receipt model) |
| 3. Decide category (Fuel, Telecom, Meals…) | LLM | **Azure OpenAI** (gpt-4o-mini) |
| 4. Check policy + fraud rules | Python rules | (runs in App Service) |
| 5. Store the original bill privately | Storage | **Azure Blob Storage** |
| 6. Save the claim | Database | SQLite on App Service's persistent `/home` |
| 7. HR reviews, approves or rejects | Web dashboard | App Service |
| 8. Every push to `main` is tested and deployed | CI/CD | **GitHub Actions → App Service** |

## 1b. Smart Benefit Rebalancer (the feature built from real user feedback)

**The user problem.** A user of the Prosperr Benefits app wrote in an App Store review that
employees should be able to decide how to use their flexi benefits in real time, for example
when a school fee or an unplanned medical expense comes up mid-month. With fixed category
limits, unused money in one category is wasted even when it's needed in another.

**How this app solves it:**

| Employee problem | What the app does |
|---|---|
| "I don't know what's left" | **My benefits** tab shows live used / left per category for this month |
| "An unplanned expense came up" | Employee requests: *move Rs 1,000 from Books & Learning to Telecom*, with a reason |
| "The system decides, not me" | Employee chooses the move; HR approves or rejects in one click |
| "Will this break payroll or tax?" | The **total monthly budget never changes**, only how it's split |

**Rules that keep it safe (in `app/services/benefits.py`):**
- You can only move money that is **unused**: limit minus bills already claimed minus money already waiting to move.
- While a request is pending, that money is **held**, so the same rupees can't be moved twice.
- "Other" isn't part of the flexi plan, so it can't send or receive money. Minimum move is Rs 100.
- When HR clicks Approve, the app **checks again**, because the employee may have uploaded more bills since asking. If the money is no longer free, approval is blocked.
- Rejecting needs a comment, and frees the held money straight away.
- New bills are checked against the employee's **own updated limits**, not the company defaults.

**Design choice: an audit trail, not edited limits.** Default limits are never overwritten.
Each move is saved as a row in `rebalance_requests`, and a person's limit is calculated as
*default + approved moves in - approved moves out*. So HR can always see who moved what,
when and why, which matters for tax and compliance.

**Endpoints:**

| Method | Path | What it does |
|---|---|---|
| GET | `/api/benefits?employee=Name` | Live balance for this month, per category |
| POST | `/api/rebalance` | Employee asks to move money (`from_category`, `to_category`, `amount`, `reason`) |
| GET | `/api/rebalance?status=pending` | List requests (HR view shows how much is still free) |
| POST | `/api/rebalance/{id}/decision` | HR approves or rejects |

**Honest limitation:** in a real company, a change like this may also need to reach payroll
and follow the company's own FBP rules (for example, some companies only allow changes
at set times). This prototype keeps the total fixed and requires HR approval to respect that,
but it doesn't connect to a payroll system.

## 2. Architecture

```
 Employee / HR (browser)
          │
          ▼
 ┌─────────────────────────── Azure App Service (Linux, Python 3.11) ───────────────────────────┐
 │  FastAPI app                                                                                 │
 │   POST /api/claims ──► ocr.py ──────────► Azure AI Document Intelligence (prebuilt-receipt)   │
 │                    ──► classifier.py ───► Azure OpenAI (gpt-4o-mini)                          │
 │                    ──► rules.py (limits, duplicates, dates, round amounts)                    │
 │                    ──► storage.py ──────► Azure Blob Storage (private "bills" container)      │
 │                    ──► db.py ───────────► SQLite in /home/data (persistent)                  │
 │   GET  /api/claims, /api/summary, /api/claims/{id}/bill                                      │
 │   POST /api/claims/{id}/decision (approve / reject)                                          │
 │   /  → static frontend (HTML + CSS + JS)                                                      │
 └──────────────────────────────────────────────────────────────────────────────────────────────┘
          ▲
          │ deploy (zip) + health check
 GitHub Actions: push to main → install → pytest → deploy → curl /health
```

**Smart fallback:** if any Azure service isn't configured, the app still works using a local
version (fake OCR, keyword classifier, local folder). That's why tests run in CI for free, and
why you can build the whole thing on your laptop before touching Azure.

## 3. Folder structure: what each file does

```
reimburse-ai/
├── app/
│   ├── main.py              # The web server. All API routes; glues every step together
│   ├── config.py            # Reads settings/keys from environment variables
│   ├── db.py                # The "claims" database table (SQLAlchemy)
│   ├── services/
│   │   ├── ocr.py           # Reads the bill with Azure Document Intelligence
│   │   ├── classifier.py    # Picks the category with Azure OpenAI (keyword fallback)
│   │   ├── rules.py         # Policy limits + fraud checks → list of warnings
│   │   ├── benefits.py      # Smart Benefit Rebalancer: balances + move rules
│   │   └── storage.py       # Saves/loads bill files in Azure Blob Storage
│   └── static/              # Frontend: index.html, style.css, app.js
├── tests/                   # 32 automated tests (rules, rebalancer, full API)
├── infra/setup-azure.sh     # One script that creates all Azure resources
├── .github/workflows/deploy.yml   # CI/CD pipeline
├── requirements.txt         # Python packages for the app
├── requirements-dev.txt     # Extra packages for testing
└── .env.example             # Template for your local secrets
```

### How the code works, file by file

**`main.py` → `submit_claim()`** is the heart of the app. When a bill is uploaded it:
1. Checks the name, file type (JPG/PNG/PDF) and size (max 5 MB).
2. Makes a SHA-256 hash of the file: a unique fingerprint, used to catch the same bill uploaded twice.
3. Calls `ocr.extract_receipt()` → gets vendor, date, total, items, confidence.
4. Calls `classifier.classify()` → gets category + a one-line reason.
5. Loads the employee's earlier claims and calls `rules.check_flags()` → list of warnings.
6. Saves the file with `storage.save_bill()`, then saves the claim row in the database.
7. Returns everything as JSON, which the frontend draws as a receipt.

**`ocr.py`** sends the file to Document Intelligence's `prebuilt-receipt` model. This model is
already trained on millions of receipts, so we don't train anything. It returns *fields*
(`MerchantName`, `TransactionDate`, `Total`, `Items`), each with a confidence score. We convert
them into a simple dict.

**`classifier.py`** sends vendor + items + bill text to GPT with a strict prompt: *pick one of
these 6 categories and reply only in JSON*. We use `response_format={"type": "json_object"}` so
the answer is always valid JSON, and we double-check the category is in our list (never trust
an LLM blindly). If Azure OpenAI fails, it falls back to keyword matching, so the app never breaks.

**`rules.py`** is pure Python (easy to test). It flags:
- missing total / vendor / date
- date in the future, or bill older than 90 days
- low scan confidence (< 60%)
- **exact duplicate** (same file hash) or **likely duplicate** (same vendor + amount + date, e.g. a new photo of the same bill)
- **over the monthly limit** for that category (rejected claims don't count)
- category not covered by policy
- suspiciously round big amounts (common in fake bills)

Flags don't auto-reject; they help HR decide. That's a deliberate product choice: AI assists, humans decide.

**`storage.py`** keeps bills in a **private** Blob container. HR views them through
`/api/claims/{id}/bill`, so the files are never publicly reachable.

**`db.py`** defines one table, `claims`. SQLite lives in `/home/data` on Azure because App
Service keeps `/home` across restarts and deployments.

**Frontend (`static/`)**: plain HTML/CSS/JS, no build step. The employee tab uploads with
`fetch()` and shows the result as a receipt. The HR tab shows stats, a filterable table, and a
review window with the original bill next to the AI-read details.

## 4. Run it on your laptop (no Azure needed yet)

```bash
git clone https://github.com/<you>/reimburse-ai.git
cd reimburse-ai
python -m venv .venv
# Windows: .venv\Scripts\activate    Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env          # leave the keys empty for now
uvicorn app.main:app --reload
```
Open http://localhost:8000. Upload any image: the fake OCR fills in realistic data.

Run the tests:
```bash
pytest -v
```

Want real AI locally? Fill in `DOCINTEL_*` and `AOAI_*` in `.env` after step 5.
API docs are auto-generated at http://localhost:8000/docs (nice to show in interviews).

## 5. Create the Azure resources

You need an Azure account. As a student, use **Azure for Students** (free credit, no card).

1. Open **Azure Cloud Shell** (the `>_` icon at the top of portal.azure.com), choose **Bash**.
2. Upload `infra/setup-azure.sh` (or paste it), then:
   ```bash
   bash setup-azure.sh
   ```
3. It creates: resource group, storage account + `bills` container, Document Intelligence (free
   F0 tier), Azure OpenAI with a `gpt-4o-mini` deployment, App Service plan + web app, all the
   app settings, and the startup command.
4. At the end it prints your **app name** and a big block of XML (the **publish profile**). Keep both for step 6.

> **If Azure OpenAI creation fails** (some student subscriptions don't allow it, or the region
> has no quota), the script continues and the app uses the keyword classifier. You can add
> OpenAI later in App Service → Configuration.
>
> **Costs:** Document Intelligence F0 is free (500 pages/month). App Service B1 costs a little
> per month and comes out of your student credit. Stop the web app when you're not demoing, or
> delete the resource group when done: `az group delete -n rg-reimburse-ai`.

## 6. Set up CI/CD with GitHub Actions

1. Push the project to a new GitHub repo.
2. In GitHub: **Settings → Secrets and variables → Actions → New repository secret**
   - Name: `AZURE_WEBAPP_PUBLISH_PROFILE`
   - Value: the whole XML printed by the setup script
3. In `.github/workflows/deploy.yml`, change `AZURE_WEBAPP_NAME` to your app's name.
4. Commit and push to `main`. Go to the **Actions** tab and watch it run.

**What the pipeline does:**

```
Pull request ──► test job: install → pytest              (no deploy)
Push to main ──► test job ──► deploy job: zip app → deploy to App Service → curl /health
```
- **CI (test job):** every PR and push runs all 32 tests. Tests use the local fallbacks, so no Azure keys are needed in GitHub.
- **CD (deploy job):** runs only if tests pass and only on `main`. It zips the app, deploys it, and Azure installs the packages (`SCM_DO_BUILD_DURING_DEPLOYMENT=true`).
- **Smoke test:** calls `/health` on the live site until it answers. If the site doesn't start, the pipeline turns red.

Add the badge to the top of this README:
```md
![CI/CD](https://github.com/<you>/reimburse-ai/actions/workflows/deploy.yml/badge.svg)
```

## 7. Troubleshooting

| Problem | Fix |
|---|---|
| Deploy step says unauthorized | Re-download the publish profile (`az webapp deployment list-publishing-profiles ... --xml`) and update the secret; make sure the setup script's "basic publishing credentials" step ran |
| Site shows "Application Error" | Portal → App Service → **Log stream**. Usually a wrong startup command or a missing app setting |
| `/health` shows `"azure_ocr": false` | `DOCINTEL_ENDPOINT` / `DOCINTEL_KEY` app settings are missing or misspelled |
| Everything is categorised by keyword | Azure OpenAI isn't configured, or the deployment name isn't `gpt-4o-mini` |
| Scan fails with 401 | Wrong Document Intelligence key; copy key1 again from "Keys and Endpoint" |

## 8. Ideas to take it further

- Login with **Microsoft Entra ID** so employees see only their own claims and only HR sees the review tab
- Move from SQLite to **Azure Database for PostgreSQL** (just change `DATABASE_URL` + add `psycopg2-binary`)
- Use **Managed Identity + Key Vault** instead of keys in app settings
- Detect **GSTIN** on bills and validate its format
- Monthly **CSV export** for payroll
- **Application Insights** for monitoring
