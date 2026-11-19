# Product 21 — AI-Assisted Smart Banking and Financial Analytics Platform

## Start the application (Windows)
1. Install Python 3.10 or newer (Python 3.11 is a safe choice).
2. Extract this ZIP and open the `SmartBanking` folder in VS Code.
3. Open a terminal in that folder.
4. Create and activate an environment:
   ```powershell
   py -3.11 -m venv .venv
   .venv\Scripts\activate
   ```
5. Install packages:
   ```powershell
   python -m pip install --upgrade pip
   pip install -r requirements.txt
   ```
6. Copy `.env.example` to `.env`. Set unique `SECRET_KEY` and `JWT_SECRET_KEY` values. Add a Stripe test secret key as `STRIPE_SECRET_KEY` to enable Checkout deposits.
7. Start the application:
   ```powershell
   python app.py
   ```
8. Open `http://127.0.0.1:5000`.
9. Create a customer through Register. Create an administrator with `python make_admin.py`.
10. API health check: `http://127.0.0.1:5000/api/v1/health`.

## Test
```powershell
pytest -q
```
Run tests after installing dependencies. Do not report tests as passed unless this command completes successfully on your machine.

## Phase coverage and honest implementation status

| Phase | Included in this project | Important qualification |
|---|---|---|
| 1 — Customer/account management | Registration/login, customer profiles, savings/current accounts, deposits, withdrawals, internal transfers, loans/repayments, credit-card demo ledger, transaction history, SQLite persistence | Credit-card operations are an educational internal ledger, not a card network. |
| 2 — Analytics | Pandas-based spending analysis, budgets, savings/financial-health indicators, forecasts, month comparison, charts and CSV export | Forecasts are estimates. Investment portfolio analytics are not a complete live investment service. |
| 3 — Decision support/fraud | Rule-based suspicious-transaction alerts, mock credit score, loan recommendation logic, admin loan review, alert resolution, tests | Recommendations are advisory; admin approval is required. Thresholds/false positives should be evaluated and documented. |
| 4 — Digital portal/API | Responsive Flask customer/admin portal, forms and navigation, versioned JWT REST API | The current UI is Flask-rendered, not a separate React.js SPA. Bill-payments, investment tracking and customer-support workflows are not fully implemented in this baseline. |
| 5 — Service integration | Frankfurter currency-rate lookup and conversion; Stripe Checkout deposits with server-side payment verification; Razorpay order creation when test credentials are configured with mock fallback; SMTP email when configured; mock credit-score API; JWT API authentication | Stripe deposits require a configured test secret key; no payment is simulated when unavailable. |
| 6 — Integration/governance | Combined application, API guide, Dockerfile, environment template, run guide and governance checklist | Cloud deployment, production security review and payment webhook verification require separate configuration and testing. |

## Important API reference
See `API_GUIDE.md` for versioned REST endpoints, bearer-token usage, request examples and integration notes. All protected API calls require `Authorization: Bearer <access_token>`.

## Security and academic-use notice
This is an academic demonstration, not production banking software. Never use real customer data, real card details or real funds. Keep secrets in `.env`, do not commit credentials, use HTTPS for deployment, enforce account ownership, and use payment provider test mode only. Mock services must never be described as live integrations.
