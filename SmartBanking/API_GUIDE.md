# Smart Banking REST API (v1)

Start the Flask app, then check `GET /api/v1/health`.

## Authentication
1. `POST /api/v1/auth/login` with JSON `{"email":"...","password":"..."}`.
2. Copy `access_token`.
3. Send `Authorization: Bearer <access_token>` on protected endpoints.
4. Tokens expire after 8 hours. Use HTTPS and rotate `JWT_SECRET_KEY` outside local demos.

## Endpoints
| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/v1/health` | Health check |
| POST | `/api/v1/auth/login` | JWT login |
| GET | `/api/v1/me` | Current profile |
| GET, POST | `/api/v1/accounts` | List/create accounts |
| GET | `/api/v1/transactions` | Recent transaction history |
| POST | `/api/v1/accounts/{id}/deposit` | Deposit |
| POST | `/api/v1/accounts/{id}/withdraw` | Withdraw |
| POST | `/api/v1/transfers` | Transfer between own accounts |
| GET, POST | `/api/v1/loans` | List/apply for loans |
| GET | `/api/v1/analytics` | Spending and balance metrics |
| GET | `/api/v1/credit-score` | Mock credit score |
| GET | `/api/v1/integrations/currency?base=INR&symbols=USD,EUR` | Live currency rates from Frankfurter |
| POST | `/api/v1/integrations/payments/orders` | Razorpay order or clearly-labelled local mock order |
| POST | `/api/v1/integrations/notifications/test` | SMTP email, or development-log simulation |
| GET | `/api/v1/admin/fraud-alerts` | Admin-only fraud alerts |
| GET | `/api/v1/admin/loan-decisions` | Admin-only decision recommendations |

All request bodies use JSON. Amounts are INR unless otherwise stated. API responses contain `success` and either result data or an `error`.

## Integration truth table
- **Currency:** live external Frankfurter API request; internet connectivity is required.
- **Payments:** actual Razorpay order creation only when `RAZORPAY_KEY_ID` and `RAZORPAY_KEY_SECRET` are configured (use Razorpay test keys). Without keys, the API creates a persisted `mock_sandbox` order and explicitly does not charge money. This is a demonstration fallback, not a completed live payment.
- **Credit score:** mock service is used because the brief permits a Mock API.
- **Notifications:** SMTP sends an email only when all SMTP settings are configured. Otherwise it logs a development simulation and returns `status=simulated`; it does not claim an email was sent.
- **Stripe Checkout deposits:** available from the signed-in customer dashboard when `STRIPE_SECRET_KEY` is configured. The server verifies the completed Checkout session with Stripe before crediting the selected account. Without the key, deposits are unavailable and no payment is simulated.

## Example
```bash
curl -X POST http://127.0.0.1:5000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"customer@example.com","password":"your-password"}'
```


## Dashboard integration
The customer dashboard calls protected REST API features using a short-lived JWT issued for the signed-in user. It provides:
- Internal transfers between the customer's own accounts.
- Live currency conversion using the Frankfurter provider.
- Stripe Checkout deposits, with server-side payment verification.
- Payment order creation and recent order mode.
- SMTP notification test status.
- Fraud alert count and links to analytics, loans and cards.

## Important limitations
- **Currency rates are live external data** and require internet access. If Frankfurter is unavailable, the dashboard shows an error instead of invented rates.
- **Razorpay:** set `RAZORPAY_KEY_ID` and `RAZORPAY_KEY_SECRET` to create an order in the configured Razorpay account. Without credentials, the app creates a local mock order; no money is charged. An order is not a completed payment.
- **Stripe:** set `STRIPE_SECRET_KEY` in `.env` and use test credentials for demos. The account is credited only after server-side Stripe verification; repeated success redirects do not credit it again.
- **Email:** SMTP credentials are required to send email. Without them, the API returns `development_log` simulation; no email is sent.
- **Credit score and loan recommendations remain demo decision-support logic**, not a bank or credit-bureau API.


## Provider credential setup
- **Frankfurter currency rates:** no API key is required. The app calls its public exchange-rate endpoint from the Flask server.
- **Stripe Checkout:** set `STRIPE_SECRET_KEY` in `.env` (use a test secret key for demos), then restart Flask. The key is never sent to the browser.
- **Razorpay:** put `RAZORPAY_KEY_ID` and `RAZORPAY_KEY_SECRET` in your local `.env` file (use test credentials for the college demo). Restart Flask after changing `.env`.
- **SMTP email:** set `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, and `SMTP_FROM` in `.env`, then restart Flask.
- **JWT:** set a long, random `JWT_SECRET_KEY` in `.env`. The dashboard never displays the secret or provider keys; it only displays whether configuration is present.

The dashboard's API & Authentication Status table lists the connected provider, endpoint and whether the integration is live, configured, or simulated. Do not paste secret keys into screenshots, reports, or source control.
