"""Versioned REST API for the Smart Banking project.

Authentication: POST /api/v1/auth/login, then send Authorization: Bearer <token>.
Set JWT_SECRET_KEY in the environment before deployment.
"""
import os
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps

import jwt
import requests
from flask import Blueprint, jsonify, request, current_app
from flask_login import current_user

from models import (
    db, User, Customer, Account, Transaction, Loan, CreditCard,
    FraudAlert, Budget, PaymentOrder
)
from credit_score import get_mock_credit_score
from loan_decision import recommend_loan
from fraud import check_transaction_for_fraud

api = Blueprint("api_v1", __name__, url_prefix="/api/v1")


def _json_error(message, status=400):
    return jsonify({"success": False, "error": message}), status


def _token_secret():
    return current_app.config["JWT_SECRET_KEY"]


def issue_token(user):
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "role": user.role,
        "iat": now,
        "exp": now + timedelta(hours=8),
    }
    return jwt.encode(payload, _token_secret(), algorithm="HS256")


def token_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return _json_error("Bearer token is required.", 401)
        token = header.split(" ", 1)[1].strip()
        try:
            payload = jwt.decode(token, _token_secret(), algorithms=["HS256"])
            user = db.session.get(User, int(payload["sub"]))
            if not user:
                return _json_error("User no longer exists.", 401)
            request.api_user = user
        except (jwt.InvalidTokenError, ValueError, KeyError, TypeError):
            return _json_error("Invalid or expired token.", 401)
        return fn(*args, **kwargs)
    return wrapped


def admin_token_required(fn):
    @wraps(fn)
    @token_required
    def wrapped(*args, **kwargs):
        if request.api_user.role != "admin":
            return _json_error("Administrator access required.", 403)
        return fn(*args, **kwargs)
    return wrapped


def customer_for(user):
    return Customer.query.filter_by(user_id=user.id).first()


def account_json(a):
    return {
        "id": a.id, "account_number": a.account_number,
        "account_type": a.account_type, "balance": round(a.balance, 2),
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


def transaction_json(t):
    return {
        "id": t.id, "account_id": t.account_id, "type": t.type,
        "amount": round(t.amount, 2), "category": t.category,
        "note": t.note, "timestamp": t.timestamp.isoformat() if t.timestamp else None,
    }


@api.get("/health")
def health():
    return jsonify({"success": True, "service": "smart-banking-api", "version": "v1"})


@api.post("/auth/login")
def api_login():
    data = request.get_json(silent=True) or {}
    email = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", ""))
    user = User.query.filter_by(email=email).first()
    if not user or not user.check_password(password):
        return _json_error("Invalid email or password.", 401)
    return jsonify({
        "success": True, "access_token": issue_token(user), "token_type": "Bearer",
        "expires_in": 28800, "user": {"id": user.id, "email": user.email, "role": user.role},
    })


@api.get("/me")
@token_required
def me():
    user = request.api_user
    customer = customer_for(user)
    return jsonify({
        "success": True, "user": {"id": user.id, "email": user.email, "role": user.role},
        "customer": ({"id": customer.id, "full_name": customer.full_name,
                      "phone": customer.phone, "address": customer.address} if customer else None),
    })


@api.get("/accounts")
@token_required
def list_accounts():
    customer = customer_for(request.api_user)
    if not customer:
        return _json_error("Customer profile not found.", 404)
    accounts = Account.query.filter_by(customer_id=customer.id).order_by(Account.id).all()
    return jsonify({"success": True, "accounts": [account_json(a) for a in accounts]})


@api.post("/accounts")
@token_required
def create_account():
    data = request.get_json(silent=True) or {}
    kind = data.get("account_type")
    if kind not in ("savings", "current"):
        return _json_error("account_type must be savings or current.")
    customer = customer_for(request.api_user)
    if not customer:
        return _json_error("Customer profile not found.", 404)
    account = Account(customer_id=customer.id, account_type=kind, balance=0)
    db.session.add(account)
    db.session.commit()
    return jsonify({"success": True, "account": account_json(account)}), 201


@api.get("/transactions")
@token_required
def list_transactions():
    customer = customer_for(request.api_user)
    if not customer:
        return _json_error("Customer profile not found.", 404)
    account_ids = [a.id for a in customer.accounts]
    rows = (Transaction.query.filter(Transaction.account_id.in_(account_ids))
            .order_by(Transaction.timestamp.desc()).limit(200).all()) if account_ids else []
    return jsonify({"success": True, "transactions": [transaction_json(t) for t in rows]})


def _owned_account(user, account_id):
    customer = customer_for(user)
    if not customer:
        return None
    return Account.query.filter_by(id=account_id, customer_id=customer.id).first()


@api.post("/accounts/<int:account_id>/deposit")
@token_required
def api_deposit(account_id):
    account = _owned_account(request.api_user, account_id)
    if not account:
        return _json_error("Account not found.", 404)
    data = request.get_json(silent=True) or {}
    try:
        amount = round(float(data.get("amount")), 2)
    except (TypeError, ValueError):
        return _json_error("A valid amount is required.")
    if amount <= 0 or amount > 10000000:
        return _json_error("Amount must be greater than zero and no more than ₹1 crore.")
    txn = Transaction(account_id=account.id, type="deposit", amount=amount,
                      category=str(data.get("category", "general"))[:50])
    account.balance += amount
    db.session.add(txn)
    db.session.commit()
    check_transaction_for_fraud(txn)
    return jsonify({"success": True, "account": account_json(account),
                    "transaction": transaction_json(txn)}), 201


@api.post("/accounts/<int:account_id>/withdraw")
@token_required
def api_withdraw(account_id):
    account = _owned_account(request.api_user, account_id)
    if not account:
        return _json_error("Account not found.", 404)
    data = request.get_json(silent=True) or {}
    try:
        amount = round(float(data.get("amount")), 2)
    except (TypeError, ValueError):
        return _json_error("A valid amount is required.")
    if amount <= 0:
        return _json_error("Amount must be greater than zero.")
    if account.balance < amount:
        return _json_error("Insufficient balance.", 409)
    txn = Transaction(account_id=account.id, type="withdrawal", amount=amount,
                      category=str(data.get("category", "general"))[:50])
    account.balance -= amount
    db.session.add(txn)
    db.session.commit()
    check_transaction_for_fraud(txn)
    return jsonify({"success": True, "account": account_json(account),
                    "transaction": transaction_json(txn)}), 201


@api.post("/transfers")
@token_required
def api_transfer():
    data = request.get_json(silent=True) or {}
    try:
        from_id, to_id = int(data.get("from_account_id")), int(data.get("to_account_id"))
        amount = round(float(data.get("amount")), 2)
    except (TypeError, ValueError):
        return _json_error("from_account_id, to_account_id and a valid amount are required.")
    source = _owned_account(request.api_user, from_id)
    target = _owned_account(request.api_user, to_id)
    if not source or not target:
        return _json_error("Both accounts must belong to the authenticated customer.", 404)
    if from_id == to_id:
        return _json_error("Source and destination accounts must differ.")
    if amount <= 0:
        return _json_error("Amount must be greater than zero.")
    if source.balance < amount:
        return _json_error("Insufficient balance.", 409)
    source.balance -= amount
    target.balance += amount
    out_txn = Transaction(account_id=source.id, type="transfer_out", amount=amount, category="transfer")
    in_txn = Transaction(account_id=target.id, type="transfer_in", amount=amount, category="transfer")
    db.session.add_all([out_txn, in_txn])
    db.session.commit()
    check_transaction_for_fraud(out_txn)
    return jsonify({"success": True, "message": "Transfer completed.",
                    "source": account_json(source), "destination": account_json(target)}), 201


@api.get("/loans")
@token_required
def list_loans():
    customer = customer_for(request.api_user)
    loans = Loan.query.filter_by(customer_id=customer.id).order_by(Loan.created_at.desc()).all()
    return jsonify({"success": True, "loans": [
        {"id": l.id, "amount": l.amount, "interest_rate": l.interest_rate,
         "term_months": l.term_months, "status": l.status, "amount_repaid": l.amount_repaid}
        for l in loans
    ]})


@api.post("/loans")
@token_required
def apply_loan():
    data = request.get_json(silent=True) or {}
    try:
        amount = float(data.get("amount"))
        rate = float(data.get("interest_rate"))
        term = int(data.get("term_months"))
    except (TypeError, ValueError):
        return _json_error("amount, interest_rate and term_months must be valid numbers.")
    if amount <= 0 or rate < 0 or term <= 0 or amount > 100000000:
        return _json_error("Loan amount and term must be positive; interest cannot be negative.")
    customer = customer_for(request.api_user)
    loan = Loan(customer_id=customer.id, amount=amount, interest_rate=rate, term_months=term)
    db.session.add(loan)
    db.session.commit()
    return jsonify({"success": True, "loan_id": loan.id, "status": loan.status}), 201


@api.get("/analytics")
@token_required
def analytics():
    customer = customer_for(request.api_user)
    accounts = Account.query.filter_by(customer_id=customer.id).all()
    ids = [a.id for a in accounts]
    txns = Transaction.query.filter(Transaction.account_id.in_(ids)).all() if ids else []
    spend = {}
    deposits = 0.0
    for t in txns:
        if t.type in ("withdrawal", "transfer_out"):
            spend[t.category or "general"] = spend.get(t.category or "general", 0.0) + t.amount
        elif t.type in ("deposit", "transfer_in"):
            deposits += t.amount
    total_spent = sum(spend.values())
    return jsonify({
        "success": True, "total_balance": round(sum(a.balance for a in accounts), 2),
        "total_deposits": round(deposits, 2), "total_spent": round(total_spent, 2),
        "savings_rate_percent": round(((deposits-total_spent)/deposits*100), 2) if deposits else 0,
        "spending_by_category": [{"category": k, "amount": round(v, 2)}
                                 for k, v in sorted(spend.items(), key=lambda x: x[1], reverse=True)],
        "transaction_count": len(txns),
    })


@api.get("/credit-score")
@token_required
def credit_score():
    customer = customer_for(request.api_user)
    return jsonify({"success": True, "score": get_mock_credit_score(customer),
                    "source": "project mock credit-score service",
                    "note": "Replace with an authorized provider API for production use."})


@api.get("/integrations/currency")
@token_required
def currency_rates():
    base = request.args.get("base", "INR").upper()
    symbols = request.args.get("symbols", "USD,EUR,GBP").upper()
    symbol_list = [item.strip() for item in symbols.split(",") if item.strip()]
    if not base.isalpha() or len(base) != 3:
        return _json_error("base must be a 3-letter currency code.")
    if not symbol_list or len(symbol_list) > 5 or any(not item.isalpha() or len(item) != 3 for item in symbol_list):
        return _json_error("symbols must contain up to five comma-separated 3-letter currency codes.")
    try:
        response = requests.get(
            "https://api.frankfurter.app/latest",
            params={"from": base, "to": ",".join(symbol_list)},
            timeout=8,
        )
        response.raise_for_status()
        result = response.json()
        return jsonify({"success": True, "provider": "Frankfurter",
                        "base": result.get("base"), "date": result.get("date"),
                        "rates": result.get("rates", {})})
    except requests.RequestException:
        return _json_error("Currency provider is temporarily unavailable. Try again later.", 502)


@api.post("/integrations/payments/orders")
@token_required
def create_payment_order():
    data = request.get_json(silent=True) or {}
    try:
        amount = round(float(data.get("amount")), 2)
    except (TypeError, ValueError):
        return _json_error("A valid amount is required.")
    if amount <= 0 or amount > 100000:
        return _json_error("Payment amount must be between ₹0.01 and ₹100,000.")
    key_id = os.environ.get("RAZORPAY_KEY_ID")
    key_secret = os.environ.get("RAZORPAY_KEY_SECRET")
    if key_id and key_secret:
        try:
            response = requests.post(
                "https://api.razorpay.com/v1/orders",
                auth=(key_id, key_secret),
                json={"amount": int(round(amount * 100)), "currency": "INR",
                      "receipt": f"sb-{uuid.uuid4().hex[:18]}"},
                timeout=12,
            )
            response.raise_for_status()
            payload = response.json()
            provider_id, mode = payload["id"], "razorpay_test_or_live"
        except (requests.RequestException, KeyError, ValueError):
            return _json_error("Razorpay order creation failed. Check test keys and provider status.", 502)
    else:
        # Explicit local sandbox stub: never claims to charge a real card.
        provider_id, mode = "mock_order_" + uuid.uuid4().hex[:16], "mock_sandbox"
    customer = customer_for(request.api_user)
    order = PaymentOrder(customer_id=customer.id, provider="razorpay",
                         provider_order_id=provider_id, amount=amount,
                         currency="INR", status="created", mode=mode)
    db.session.add(order)
    db.session.commit()
    return jsonify({
        "success": True, "order": {"id": order.id, "provider_order_id": provider_id,
        "amount": amount, "currency": "INR", "status": order.status, "mode": mode},
        "message": ("Razorpay order created; complete checkout with the Razorpay test checkout."
                    if mode != "mock_sandbox" else
                    "Mock sandbox order created. No real money was charged; configure Razorpay test keys for gateway checkout.")
    }), 201


@api.post("/integrations/notifications/test")
@token_required
def test_notification():
    data = request.get_json(silent=True) or {}
    recipient = str(data.get("email", request.api_user.email)).strip()
    subject = str(data.get("subject", "Smart Banking notification test"))[:120]
    message = str(data.get("message", "Your Smart Banking notification integration is configured."))[:2000]
    host = os.environ.get("SMTP_HOST")
    if host:
        import smtplib
        from email.message import EmailMessage
        try:
            msg = EmailMessage()
            msg["Subject"], msg["From"], msg["To"] = subject, os.environ["SMTP_FROM"], recipient
            msg.set_content(message)
            with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587")), timeout=10) as smtp:
                smtp.starttls()
                smtp.login(os.environ["SMTP_USERNAME"], os.environ["SMTP_PASSWORD"])
                smtp.send_message(msg)
            return jsonify({"success": True, "channel": "email", "mode": "smtp",
                            "recipient": recipient, "status": "sent"})
        except (OSError, KeyError, ValueError):
            return _json_error("Email delivery failed. Check SMTP configuration.", 502)
    current_app.logger.info("DEMO NOTIFICATION to=%s subject=%s body=%s", recipient, subject, message)
    return jsonify({"success": True, "channel": "email", "mode": "development_log",
                    "recipient": recipient, "status": "simulated",
                    "note": "No email was sent. Configure SMTP_HOST and SMTP credentials to send email."})


@api.get("/admin/fraud-alerts")
@admin_token_required
def admin_fraud_alerts():
    rows = FraudAlert.query.order_by(FraudAlert.created_at.desc()).limit(200).all()
    return jsonify({"success": True, "alerts": [
        {"id": a.id, "transaction_id": a.transaction_id, "reason": a.reason,
         "severity": a.severity, "resolved": a.resolved}
        for a in rows
    ]})


@api.get("/admin/loan-decisions")
@admin_token_required
def admin_loan_decisions():
    loans = Loan.query.order_by(Loan.created_at.desc()).all()
    output = []
    for loan in loans:
        score = get_mock_credit_score(loan.customer)
        balance = sum(a.balance for a in loan.customer.accounts)
        decision, reason = recommend_loan(score, loan.amount, balance)
        output.append({"loan_id": loan.id, "suggested_decision": decision,
                       "reason": reason, "credit_score": score,
                       "status": loan.status})
    return jsonify({"success": True, "recommendations": output})


