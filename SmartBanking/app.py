import os
from dotenv import load_dotenv
load_dotenv()
import csv
import io
import base64
import math
from datetime import datetime, timezone, timedelta

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")  # no GUI backend needed on a server
import matplotlib.pyplot as plt

from flask import Flask, render_template, request, redirect, url_for, flash, Response
from flask_login import LoginManager, login_user, logout_user, login_required, current_user

from models import db, User, Customer, Account, Transaction, Loan, CreditCard, FraudAlert, Budget, PaymentOrder, StripeDeposit
from fraud import check_transaction_for_fraud
from credit_score import get_mock_credit_score
from loan_decision import recommend_loan
from api import api as api_blueprint, issue_token

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only-change-this-before-deployment")
app.config["JWT_SECRET_KEY"] = os.environ.get("JWT_SECRET_KEY", app.config["SECRET_KEY"])
database_url = os.environ.get("DATABASE_URL", "sqlite:///smart_banking.db")

if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = database_url

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False


@app.route("/")
def home():
    if current_user.is_authenticated:
        if current_user.is_admin():
            return redirect(url_for("admin_dashboard"))
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


db.init_app(app)
app.register_blueprint(api_blueprint)


@app.errorhandler(405)
def method_not_allowed(_error):
    flash("That action is not available with this request method.", "error")
    return redirect(url_for("dashboard"))


login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.init_app(app)


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def admin_required(view_func):
    from functools import wraps

    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin():
            flash("Admin access required.", "error")
            return redirect(url_for("dashboard"))
        return view_func(*args, **kwargs)

    return wrapper


# ---------------------------------------------------------------- AUTH ----

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password = request.form["password"]
        full_name = request.form["full_name"].strip()
        phone = request.form.get("phone", "").strip()
        address = request.form.get("address", "").strip()

        if User.query.filter_by(email=email).first():
            flash("Email already registered.", "error")
            return redirect(url_for("register"))

        user = User(email=email, role="customer")
        user.set_password(password)
        db.session.add(user)
        db.session.flush()

        customer = Customer(user_id=user.id, full_name=full_name, phone=phone, address=address)
        db.session.add(customer)
        db.session.commit()

        flash("Registered successfully. Please log in.", "success")
        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password = request.form["password"]
        user = User.query.filter_by(email=email).first()

        if user and user.check_password(password):
            login_user(user)
            flash("Logged in successfully.", "success")
            if user.is_admin():
                return redirect(url_for("admin_dashboard"))
            return redirect(url_for("dashboard"))

        flash("Invalid email or password.", "error")

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Logged out.", "success")
    return redirect(url_for("login"))


# ----------------------------------------------------------- CUSTOMER -----

@app.route("/dashboard")
@login_required
def dashboard():
    if current_user.is_admin():
        return redirect(url_for("admin_dashboard"))

    customer = current_user.customer
    accounts = Account.query.filter_by(customer_id=customer.id).all()
    account_ids = [a.id for a in accounts]
    recent_txns = (
        Transaction.query.filter(Transaction.account_id.in_(account_ids))
        .order_by(Transaction.timestamp.desc())
        .limit(15)
        .all()
        if account_ids
        else []
    )
    total_balance = sum(a.balance for a in accounts)
    recent_orders = (
        PaymentOrder.query.filter_by(customer_id=customer.id)
        .order_by(PaymentOrder.created_at.desc())
        .limit(5)
        .all()
    )
    open_fraud_alerts = (
        FraudAlert.query.join(Transaction)
        .filter(Transaction.account_id.in_(account_ids), FraudAlert.resolved.is_(False))
        .count()
        if account_ids
        else 0
    )
    recent_fraud_alerts = (
        FraudAlert.query.join(Transaction)
        .filter(Transaction.account_id.in_(account_ids), FraudAlert.resolved.is_(False))
        .order_by(FraudAlert.created_at.desc())
        .limit(5)
        .all()
        if account_ids
        else []
    )
    dashboard_api_token = issue_token(current_user)

    return render_template(
        "dashboard.html",
        accounts=accounts,
        transactions=recent_txns,
        total_balance=total_balance,
        recent_orders=recent_orders,
        open_fraud_alerts=open_fraud_alerts,
        recent_fraud_alerts=recent_fraud_alerts,
        fraud_status="Review recommended" if open_fraud_alerts else "No active alerts",
        dashboard_api_token=dashboard_api_token,
        razorpay_configured=bool(os.environ.get("RAZORPAY_KEY_ID") and os.environ.get("RAZORPAY_KEY_SECRET")),
        jwt_secret_configured=bool(os.environ.get("JWT_SECRET_KEY")),
        stripe_configured=bool(os.environ.get("STRIPE_SECRET_KEY")),
        smtp_configured=bool(
            os.environ.get("SMTP_HOST")
            and os.environ.get("SMTP_USERNAME")
            and os.environ.get("SMTP_PASSWORD")
            and os.environ.get("SMTP_FROM")
        ),
    )


@app.route("/accounts/new", methods=["POST"])
@login_required
def new_account():
    account_type = request.form["account_type"]
    if account_type not in ("savings", "current"):
        flash("Invalid account type.", "error")
        return redirect(url_for("dashboard"))

    account = Account(customer_id=current_user.customer.id, account_type=account_type)
    db.session.add(account)
    db.session.commit()
    flash(f"{account_type.title()} account created.", "success")
    return redirect(url_for("dashboard"))


def _get_own_account_or_none(account_id):
    account = db.session.get(Account, account_id)
    if not current_user.customer or not account or account.customer_id != current_user.customer.id:
        return None
    return account


def _primary_account(customer):
    """The customer's first-opened account, used for loan payouts and payments."""
    return Account.query.filter_by(customer_id=customer.id).order_by(Account.id).first()


@app.route("/accounts/<int:account_id>/deposit", methods=["POST"])
@login_required
def deposit(account_id):
    account = _get_own_account_or_none(account_id)
    if not account:
        flash("Account not found.", "error")
        return redirect(url_for("dashboard"))

    amount = float(request.form["amount"])
    category = request.form.get("category", "").strip() or "general"
    if amount <= 0:
        flash("Amount must be positive.", "error")
        return redirect(url_for("dashboard"))

    account.balance += amount
    txn = Transaction(account_id=account.id, type="deposit", amount=amount, category=category)
    db.session.add(txn)
    db.session.commit()
    check_transaction_for_fraud(txn)

    flash(f"Deposited ₹{amount:.2f}.", "success")
    return redirect(url_for("dashboard"))


@app.route("/accounts/<int:account_id>/withdraw", methods=["POST"])
@login_required
def withdraw(account_id):
    account = _get_own_account_or_none(account_id)
    if not account:
        flash("Account not found.", "error")
        return redirect(url_for("dashboard"))

    amount = float(request.form["amount"])
    category = request.form.get("category", "").strip() or "general"
    if amount <= 0:
        flash("Amount must be positive.", "error")
        return redirect(url_for("dashboard"))
    if account.balance < amount:
        flash("Insufficient balance.", "error")
        return redirect(url_for("dashboard"))

    account.balance -= amount
    txn = Transaction(account_id=account.id, type="withdrawal", amount=amount, category=category)
    db.session.add(txn)
    db.session.commit()
    check_transaction_for_fraud(txn)

    flash(f"Withdrew ₹{amount:.2f}.", "success")
    return redirect(url_for("dashboard"))


@app.route("/accounts/transfer", methods=["POST"])
@app.route("/transfer", methods=["POST"])
@login_required
def transfer_between_accounts():
    try:
        from_id = int(request.form.get("from_account_id", ""))
        to_id = int(request.form.get("to_account_id", ""))
        amount = float(request.form.get("amount", ""))
    except (TypeError, ValueError):
        flash("Select valid accounts and enter a valid amount.", "error")
        return redirect(url_for("dashboard"))

    if from_id == to_id:
        flash("Please select two different accounts.", "error")
        return redirect(url_for("dashboard"))

    from_acc = _get_own_account_or_none(from_id)
    to_acc = _get_own_account_or_none(to_id)

    if not from_acc or not to_acc:
        flash("Account not found.", "error")
        return redirect(url_for("dashboard"))
    if not math.isfinite(amount) or amount <= 0:
        flash("Amount must be positive.", "error")
        return redirect(url_for("dashboard"))
    if from_acc.balance < amount:
        flash("Insufficient balance in source account.", "error")
        return redirect(url_for("dashboard"))

    from_acc.balance -= amount
    to_acc.balance += amount

    txn_out = Transaction(account_id=from_acc.id, type="transfer_out", amount=amount, category="transfer")
    txn_in = Transaction(account_id=to_acc.id, type="transfer_in", amount=amount, category="transfer")
    db.session.add_all([txn_out, txn_in])
    db.session.commit()
    check_transaction_for_fraud(txn_out)
    check_transaction_for_fraud(txn_in)

    flash(f"Transferred ₹{amount:.2f} from #{from_acc.id} to #{to_acc.id}.", "success")
    return redirect(url_for("dashboard"))


# --------------------------------------------------- STRIPE GATEWAY -------

@app.route("/payments/stripe-deposit", methods=["POST"])
@login_required
def stripe_deposit():
    try:
        account_id = int(request.form.get("account_id", ""))
        amount = float(request.form.get("amount", ""))
    except (TypeError, ValueError):
        flash("Select an account and enter a valid deposit amount.", "error")
        return redirect(url_for("dashboard"))

    account = _get_own_account_or_none(account_id)
    if not account:
        flash("Select one of your accounts for the deposit.", "error")
        return redirect(url_for("dashboard"))
    if not math.isfinite(amount) or amount <= 0 or round(amount * 100) < 1:
        flash("Amount must be positive.", "error")
        return redirect(url_for("dashboard"))
    stripe_key = os.environ.get("STRIPE_SECRET_KEY")
    if not stripe_key:
        flash("Stripe deposits are unavailable. Configure STRIPE_SECRET_KEY in .env.", "error")
        return redirect(url_for("dashboard"))

    import stripe
    amount_cents = int(round(amount * 100))
    try:
        session = stripe.checkout.Session.create(
            api_key=stripe_key,
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "inr",
                    "product_data": {"name": f"SmartBanking Deposit (#{account.id})"},
                    "unit_amount": amount_cents,
                },
                "quantity": 1,
            }],
            mode="payment",
            client_reference_id=str(current_user.customer.id),
            metadata={
                "customer_id": str(current_user.customer.id),
                "account_id": str(account.id),
            },
            success_url=url_for("stripe_success", _external=True) + "?session_id={CHECKOUT_SESSION_ID}",
            cancel_url=url_for("dashboard", _external=True),
        )
    except stripe.StripeError:
        app.logger.exception("Stripe Checkout session creation failed")
        flash("Could not start Stripe Checkout. Please try again later.", "error")
        return redirect(url_for("dashboard"))

    db.session.add(StripeDeposit(
        checkout_session_id=session.id,
        customer_id=current_user.customer.id,
        account_id=account.id,
        amount_cents=amount_cents,
    ))
    db.session.commit()
    return redirect(session.url, code=303)


@app.route("/payments/stripe-success")
@login_required
def stripe_success():
    session_id = request.args.get("session_id", "").strip()
    stripe_key = os.environ.get("STRIPE_SECRET_KEY")
    if not stripe_key:
        flash("Stripe payment cannot be verified because Stripe is not configured.", "error")
        return redirect(url_for("dashboard"))
    if not session_id:
        flash("Stripe did not provide a Checkout session to verify.", "error")
        return redirect(url_for("dashboard"))

    import stripe
    try:
        checkout = stripe.checkout.Session.retrieve(session_id, api_key=stripe_key)
    except stripe.StripeError:
        app.logger.exception("Stripe Checkout session verification failed")
        flash("Could not verify the Stripe payment. Please contact support if you were charged.", "error")
        return redirect(url_for("dashboard"))

    deposit = StripeDeposit.query.filter_by(checkout_session_id=session_id).first()
    customer = current_user.customer
    if not customer or not deposit or deposit.customer_id != customer.id:
        flash("Stripe payment session was not found for your account.", "error")
        return redirect(url_for("dashboard"))

    metadata = checkout.metadata or {}
    account = Account.query.filter_by(
        id=deposit.account_id,
        customer_id=deposit.customer_id,
    ).first()
    if (
        checkout.status != "complete"
        or checkout.payment_status != "paid"
        or checkout.mode != "payment"
        or checkout.amount_total != deposit.amount_cents
        or checkout.currency != "inr"
        or metadata.get("customer_id") != str(deposit.customer_id)
        or metadata.get("account_id") != str(deposit.account_id)
        or not account
    ):
        flash("Stripe has not confirmed a completed payment for this deposit.", "error")
        return redirect(url_for("dashboard"))

    updated = db.session.query(StripeDeposit).filter_by(
        id=deposit.id, status="pending"
    ).update({"status": "credited", "credited_at": datetime.utcnow()})
    if updated:
        amount = deposit.amount_cents / 100
        account.balance += amount
        txn = Transaction(account_id=account.id, type="deposit", amount=amount,
                          category="stripe_gateway")
        db.session.add(txn)
        db.session.commit()
        check_transaction_for_fraud(txn)
        flash(f"Stripe payment of ₹{amount:.2f} verified and credited.", "success")
    else:
        flash("This Stripe payment has already been credited.", "success")
    return redirect(url_for("dashboard"))


# ------------------------------------------------------ CUSTOMER PAGES ---

@app.route("/loans", methods=["GET", "POST"])
@login_required
def loans():
    customer = current_user.customer
    if request.method == "POST":
        try:
            amount = float(request.form["amount"])
            interest_rate = float(request.form["interest_rate"])
            term_months = int(request.form["term_months"])
        except (KeyError, TypeError, ValueError):
            flash("Loan details must be valid numbers.", "error")
            return redirect(url_for("loans"))
        if amount <= 0 or interest_rate < 0 or term_months <= 0:
            flash("Loan amount and term must be positive; interest cannot be negative.", "error")
            return redirect(url_for("loans"))
        db.session.add(Loan(customer_id=customer.id, amount=amount,
                            interest_rate=interest_rate, term_months=term_months))
        db.session.commit()
        flash("Loan application submitted.", "success")
        return redirect(url_for("loans"))
    customer_loans = Loan.query.filter_by(customer_id=customer.id).order_by(Loan.created_at.desc()).all()
    return render_template("loans.html", loans=customer_loans,
                           credit_score=get_mock_credit_score(customer))


@app.route("/loans/<int:loan_id>/repay", methods=["POST"])
@login_required
def repay_loan(loan_id):
    loan = db.session.get(Loan, loan_id)
    if not loan or loan.customer_id != current_user.customer.id or loan.status != "approved":
        flash("Loan not found or not approved.", "error")
        return redirect(url_for("loans"))
    try:
        amount = float(request.form["amount"])
    except (KeyError, TypeError, ValueError):
        flash("Repayment amount must be valid.", "error")
        return redirect(url_for("loans"))
    account = _primary_account(current_user.customer)
    remaining = loan.amount - loan.amount_repaid
    if not account:
        flash("Create an account before repaying.", "error")
    elif amount <= 0:
        flash("Repayment amount must be positive.", "error")
    elif amount > remaining:
        flash("Repayment cannot exceed the remaining loan amount.", "error")
    elif account.balance < amount:
        flash("Insufficient account balance.", "error")
    else:
        account.balance -= amount
        loan.amount_repaid += amount
        if loan.amount_repaid >= loan.amount:
            loan.status = "closed"
        db.session.add(Transaction(account_id=account.id, type="loan_repayment",
                                   amount=amount, category="loan"))
        db.session.commit()
        flash("Loan repayment recorded.", "success")
    return redirect(url_for("loans"))


@app.route("/credit-cards", methods=["GET", "POST"])
@login_required
def credit_cards():
    customer = current_user.customer
    if request.method == "POST":
        try:
            credit_limit = float(request.form["credit_limit"])
        except (KeyError, TypeError, ValueError):
            flash("Credit limit must be valid.", "error")
            return redirect(url_for("credit_cards"))
        if credit_limit <= 0:
            flash("Credit limit must be positive.", "error")
            return redirect(url_for("credit_cards"))
        db.session.add(CreditCard(customer_id=customer.id, credit_limit=credit_limit))
        db.session.commit()
        flash("Credit card issued.", "success")
        return redirect(url_for("credit_cards"))
    cards = CreditCard.query.filter_by(customer_id=customer.id).all()
    return render_template("credit_cards.html", cards=cards)


@app.route("/credit-cards/<int:card_id>/charge", methods=["POST"])
@login_required
def charge_card(card_id):
    card = db.session.get(CreditCard, card_id)
    if not card or card.customer_id != current_user.customer.id:
        flash("Credit card not found.", "error")
        return redirect(url_for("credit_cards"))
    try:
        amount = float(request.form["amount"])
    except (KeyError, TypeError, ValueError):
        flash("Charge amount must be valid.", "error")
        return redirect(url_for("credit_cards"))
    if amount <= 0:
        flash("Charge amount must be positive.", "error")
    elif card.balance + amount > card.credit_limit:
        flash("Charge exceeds the credit limit.", "error")
    else:
        card.balance += amount
        db.session.commit()
        flash("Card charged.", "success")
    return redirect(url_for("credit_cards"))


@app.route("/credit-cards/<int:card_id>/pay", methods=["POST"])
@login_required
def pay_card(card_id):
    card = db.session.get(CreditCard, card_id)
    if not card or card.customer_id != current_user.customer.id:
        flash("Credit card not found.", "error")
        return redirect(url_for("credit_cards"))
    try:
        amount = float(request.form["amount"])
    except (KeyError, TypeError, ValueError):
        flash("Payment amount must be valid.", "error")
        return redirect(url_for("credit_cards"))
    account = _primary_account(current_user.customer)
    if amount <= 0:
        flash("Payment amount must be positive.", "error")
    elif not account:
        flash("Create an account before paying a card.", "error")
    elif account.balance < amount:
        flash("Insufficient account balance.", "error")
    else:
        payment = min(amount, card.balance)
        if payment > 0:
            account.balance -= payment
            card.balance -= payment
            db.session.commit()
            flash(f"Paid ₹{payment:.2f} towards the card.", "success")
    return redirect(url_for("credit_cards"))


# -------------------------------------------------------- ANALYTICS ---

def _month_spend(customer, year, month):
    account_ids = [a.id for a in customer.accounts]
    if not account_ids:
        return 0.0
    rows = Transaction.query.filter(
        Transaction.account_id.in_(account_ids),
        Transaction.type.in_(("withdrawal", "transfer_out")),
        Transaction.timestamp >= datetime(year, month, 1),
    ).all()
    if month == 12:
        end = datetime(year + 1, 1, 1)
    else:
        end = datetime(year, month + 1, 1)
    return sum(t.amount for t in rows if t.timestamp < end)


@app.route("/analysis")
@login_required
def analysis():
    customer = current_user.customer
    account_ids = [a.id for a in customer.accounts]
    transactions = (Transaction.query.filter(Transaction.account_id.in_(account_ids))
                    .order_by(Transaction.timestamp.desc()).all() if account_ids else [])
    spending = [t for t in transactions if t.type in ("withdrawal", "transfer_out")]
    category_totals = {}
    for txn in spending:
        category = txn.category or "general"
        category_totals[category] = category_totals.get(category, 0.0) + txn.amount
    total_spent = sum(category_totals.values())
    budgets = Budget.query.filter_by(customer_id=customer.id).all()
    budget_totals = {b.category: b.amount for b in budgets}
    total_budget = sum(budget_totals.values())
    overall_pct = (total_spent / total_budget * 100) if total_budget else 0
    budget_progress = [{"category": b.category,
                        "pct": (sum(t.amount for t in spending if (t.category or "general") == b.category)
                                / b.amount * 100) if b.amount else 0}
                       for b in budgets]
    alerts = [{"category": b.category, "pct": item["pct"]}
              for b, item in zip(budgets, budget_progress) if item["pct"] >= 80]
    now = datetime.utcnow()
    spend_this_month = _month_spend(customer, now.year, now.month)
    last_month = now.replace(day=1) - timedelta(days=1)
    spend_last_month = _month_spend(customer, last_month.year, last_month.month)
    month_change_pct = ((spend_this_month - spend_last_month) / spend_last_month * 100
                        if spend_last_month else None)
    biggest_txn = spending[0] if spending else None
    forecast_next_month = round(total_spent * 1.06, 2)
    deposits = sum(a.balance for a in customer.accounts)
    health_score = max(0, min(100, int(100 - overall_pct + (20 if deposits >= total_spent else 0))))
    return render_template(
        "analysis.html", alerts=alerts, overall_pct=round(overall_pct, 1),
        total_spent=round(total_spent, 2), total_budget=round(total_budget, 2),
        on_track=overall_pct <= 100, budget_progress=budget_progress,
        category_breakdown=[{"category": k, "total": v} for k, v in category_totals.items()],
        chart_labels=list(category_totals), chart_values=list(category_totals.values()),
        top_category=max(category_totals, key=category_totals.get) if category_totals else None,
        forecast_next_month=forecast_next_month,
        health_label="Healthy" if health_score >= 70 else "Needs attention",
        health_score=health_score,
        health_msg="Your spending pattern is within a reasonable range."
        if health_score >= 70 else "Consider reducing spending or adding a budget.",
        savings_rate=round(((sum(t.amount for t in transactions if t.type in ("deposit", "transfer_in"))
                             - total_spent) /
                            sum(t.amount for t in transactions if t.type in ("deposit", "transfer_in")) * 100), 2)
        if sum(t.amount for t in transactions if t.type in ("deposit", "transfer_in")) else 0,
        biggest_txn=biggest_txn, spend_this_month=spend_this_month,
        spend_last_month=spend_last_month, month_change_pct=round(month_change_pct, 1)
        if month_change_pct is not None else None,
    )


@app.route("/analysis/budget", methods=["POST"])
@login_required
def set_budget():
    try:
        category = request.form["category"].strip().lower()
        amount = float(request.form["amount"])
    except (KeyError, TypeError, ValueError):
        flash("Budget details must be valid.", "error")
        return redirect(url_for("analysis"))
    if not category or amount <= 0:
        flash("Category and positive budget amount are required.", "error")
        return redirect(url_for("analysis"))
    budget = Budget.query.filter_by(customer_id=current_user.customer.id, category=category).first()
    if budget:
        budget.amount = amount
    else:
        db.session.add(Budget(customer_id=current_user.customer.id, category=category, amount=amount))
    db.session.commit()
    flash("Budget updated.", "success")
    return redirect(url_for("analysis"))


@app.route("/analysis/export.csv")
@login_required
def export_csv():
    customer = current_user.customer
    account_ids = [a.id for a in customer.accounts]
    transactions = (Transaction.query.filter(Transaction.account_id.in_(account_ids))
                    .order_by(Transaction.timestamp.desc()).all() if account_ids else [])
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "account_id", "type", "amount", "category", "timestamp"])
    for txn in transactions:
        writer.writerow([txn.id, txn.account_id, txn.type, txn.amount,
                         txn.category, txn.timestamp.isoformat() if txn.timestamp else ""])
    return Response(output.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=transactions.csv"})


# ----------------------------------------------------------- ADMIN ---

@app.route("/admin")
@login_required
@admin_required
def admin_dashboard():
    customers = Customer.query.all()
    all_loans = Loan.query.order_by(Loan.created_at.desc()).all()
    fraud_alerts = FraudAlert.query.filter_by(resolved=False).order_by(FraudAlert.created_at.desc()).all()
    loan_recommendations = {}
    for loan in all_loans:
        score = get_mock_credit_score(loan.customer)
        balance = sum(a.balance for a in loan.customer.accounts)
        label, reason = recommend_loan(score, loan.amount, balance)
        loan_recommendations[loan.id] = {"score": score, "label": label, "reason": reason}

    chart_base64 = None
    transactions = Transaction.query.filter(Transaction.type.in_(("withdrawal", "transfer_out"))).all()
    category_totals = {}
    for txn in transactions:
        category = txn.category or "general"
        category_totals[category] = category_totals.get(category, 0.0) + txn.amount
    if category_totals:
        fig, ax = plt.subplots()
        ax.bar(list(category_totals), list(category_totals.values()))
        ax.set_ylabel("Amount")
        ax.tick_params(axis="x", rotation=45)
        fig.tight_layout()
        buffer = io.BytesIO()
        fig.savefig(buffer, format="png")
        plt.close(fig)
        chart_base64 = base64.b64encode(buffer.getvalue()).decode("ascii")

    return render_template(
        "admin_dashboard.html", customers=customers,
        total_customers=len(customers),
        total_balance=sum(a.balance for a in Account.query.all()),
        pending_loans=Loan.query.filter_by(status="pending").count(),
        unresolved_alerts=FraudAlert.query.filter_by(resolved=False).count(),
        all_loans=all_loans, loan_recommendations=loan_recommendations,
        fraud_alerts=fraud_alerts, chart_base64=chart_base64,
    )


@app.route("/admin/loans/<int:loan_id>/status", methods=["POST"])
@login_required
@admin_required
def update_loan_status(loan_id):
    loan = db.session.get(Loan, loan_id)
    status = request.form.get("status")
    if not loan or status not in ("approved", "rejected"):
        return redirect(url_for("admin_dashboard"))
    if status == "approved":
        if loan.status != "pending":
            return redirect(url_for("admin_dashboard"))
        account = _primary_account(loan.customer)
        if not account:
            return redirect(url_for("admin_dashboard"))
        loan.status = "approved"
        account.balance += loan.amount
        db.session.add(Transaction(account_id=account.id, type="loan_disbursal",
                                   amount=loan.amount, category="loan"))
    else:
        if loan.status == "pending":
            loan.status = "rejected"
    db.session.commit()
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/fraud-alerts/<int:alert_id>/resolve", methods=["POST"])
@login_required
@admin_required
def resolve_alert(alert_id):
    alert = db.session.get(FraudAlert, alert_id)
    if alert:
        alert.resolved = True
        db.session.commit()
    return redirect(url_for("admin_dashboard"))
 

with app.app_context():
    db.create_all()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
