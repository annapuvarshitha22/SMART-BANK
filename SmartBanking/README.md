# Smart Banking — Product 21

Student demonstration app built with Flask, SQLAlchemy, SQLite, Pandas, NumPy, Matplotlib and a versioned JWT REST API.

## Run locally (Windows)
1. Install Python 3.10+.
2. Open a terminal in this folder.
3. Create and activate a virtual environment:
   ```powershell
   py -m venv .venv
   .venv\\Scripts\\activate
   ```
4. Install dependencies:
   ```powershell
   pip install -r requirements.txt
   ```
5. Copy `.env.example` to `.env` and replace the two secret values with long random values.
6. Start:
   ```powershell
   python app.py
   ```
7. Open `http://127.0.0.1:5000`.

The database tables are created automatically when `python app.py` starts. For an existing database, SQLAlchemy `create_all()` creates new tables but does not migrate changed columns; back up the database before updating.

## API and integrations
See `API_GUIDE.md`. The dashboard demonstrates account transfers, live currency conversion, Stripe Checkout deposits, API health, payment order creation, and notification integration status.

## Stripe Checkout deposits
Set `STRIPE_SECRET_KEY` in `.env` to a Stripe test secret key and restart the app. The secret stays on the server. Deposits are credited only after the server retrieves the Checkout session from Stripe and verifies that the payment is complete and paid. Without the key, Stripe deposits remain unavailable; no payment is simulated.

## Admin account
Run `python make_admin.py` and follow its prompts to grant admin role to an existing account. Do not expose an admin account in a public deployment.

## Tests
```powershell
pytest -q
```

## Demo limitations
This is not production banking software. Credit score and loan recommendations are mock/rule-based, fraud detection is rule-based, Stripe and Razorpay require test credentials for demos, and SMTP requires configuration. Never enter real bank credentials or card details.
