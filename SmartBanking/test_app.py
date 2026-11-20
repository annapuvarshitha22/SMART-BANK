import os
os.environ["DATABASE_URL"] = "sqlite:///:memory:"   # tests never touch your real database

import pytest
from app import app
from models import db, User, Customer, Account, Transaction, FraudAlert, Loan, CreditCard, StripeDeposit
from fraud import check_transaction_for_fraud
from loan_decision import recommend_loan


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.app_context():
        db.create_all()
        yield app.test_client()
        db.session.remove()
        db.drop_all()


def make_account():
    user = User(email="t@example.com", role="customer")
    user.set_password("pw12345")
    db.session.add(user)
    db.session.flush()
    customer = Customer(user_id=user.id, full_name="Test User")
    db.session.add(customer)
    db.session.flush()
    account = Account(customer_id=customer.id, account_type="savings", balance=500000.0)
    db.session.add(account)
    db.session.commit()
    return account


def add_txn(account, amount, txn_type="withdrawal"):
    txn = Transaction(account_id=account.id, type=txn_type, amount=amount, category="test")
    db.session.add(txn)
    db.session.commit()
    return txn


# ---------------- fraud rules ----------------

def test_large_transaction_is_flagged(client):
    account = make_account()
    txn = add_txn(account, 150000)
    check_transaction_for_fraud(txn)
    reasons = [a.reason for a in FraudAlert.query.all()]
    assert "Unusually large transaction amount" in reasons


def test_normal_transaction_is_not_flagged_as_large(client):
    account = make_account()
    txn = add_txn(account, 500)
    check_transaction_for_fraud(txn)
    reasons = [a.reason for a in FraudAlert.query.all()]
    assert "Unusually large transaction amount" not in reasons


def test_rapid_transactions_are_flagged(client):
    account = make_account()
    for _ in range(4):
        txn = add_txn(account, 100)
    check_transaction_for_fraud(txn)
    reasons = [a.reason for a in FraudAlert.query.all()]
    assert "Multiple transactions in a short time window" in reasons


# ---------------- loan decision support ----------------

def test_low_credit_score_is_rejected():
    label, _ = recommend_loan(450, 10000, 50000)
    assert label == "Reject"


def test_good_score_and_affordable_loan_is_approved():
    label, _ = recommend_loan(720, 50000, 20000)
    assert label == "Approve"


def test_good_score_but_huge_loan_needs_review():
    label, _ = recommend_loan(720, 500000, 20000)
    assert label == "Review"


def test_borderline_score_needs_review():
    label, _ = recommend_loan(651, 50000, 13000)
    assert label == "Review"


# ---------------- security ----------------

def test_customer_cannot_open_admin_page(client):
    client.post("/register", data={"email": "c@example.com", "password": "pw12345", "full_name": "C"})
    client.post("/login", data={"email": "c@example.com", "password": "pw12345"})
    response = client.get("/admin")
    assert response.status_code == 302
    assert "/dashboard" in response.headers["Location"]


    # ======================================================
# PHASE 1 TESTS: core banking features
# ======================================================

LOAN = {"amount": "50000", "interest_rate": "8", "term_months": "12"}


def signup_and_login(client, email="user@example.com", name="Test User"):
    client.post("/register", data={"email": email, "password": "pw12345", "full_name": name})
    client.post("/login", data={"email": email, "password": "pw12345"})


def open_account(client, account_type="savings"):
    client.post("/accounts/new", data={"account_type": account_type})


def all_accounts():
    db.session.expire_all()
    return Account.query.order_by(Account.id).all()


def balance_of(account_id):
    db.session.expire_all()
    return db.session.get(Account, account_id).balance


def first_id(model):
    db.session.expire_all()
    return model.query.first().id


def test_register_creates_user_and_customer_profile(client):
    signup_and_login(client, "a@example.com", "Asha")
    db.session.expire_all()
    user = User.query.filter_by(email="a@example.com").first()
    assert user.role == "customer"
    assert user.customer.full_name == "Asha"
    assert user.password_hash != "pw12345"  # password is stored hashed


def test_duplicate_email_is_rejected(client):
    signup_and_login(client, "dup@example.com")
    client.post("/register", data={"email": "dup@example.com", "password": "x", "full_name": "Other"})
    db.session.expire_all()
    assert User.query.filter_by(email="dup@example.com").count() == 1


def test_wrong_password_cannot_log_in(client):
    client.post("/register", data={"email": "w@example.com", "password": "pw12345", "full_name": "W"})
    client.post("/login", data={"email": "w@example.com", "password": "wrong"})
    response = client.get("/dashboard")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_open_savings_and_current_accounts(client):
    signup_and_login(client)
    open_account(client, "savings")
    open_account(client, "current")
    assert [a.account_type for a in all_accounts()] == ["savings", "current"]


def test_deposit_increases_balance(client):
    signup_and_login(client)
    open_account(client)
    acc_id = all_accounts()[0].id
    client.post(f"/accounts/{acc_id}/deposit", data={"amount": "1000", "category": "salary"})
    assert balance_of(acc_id) == 1000.0


def test_withdraw_decreases_balance(client):
    signup_and_login(client)
    open_account(client)
    acc_id = all_accounts()[0].id
    client.post(f"/accounts/{acc_id}/deposit", data={"amount": "1000", "category": "salary"})
    client.post(f"/accounts/{acc_id}/withdraw", data={"amount": "400", "category": "food"})
    assert balance_of(acc_id) == 600.0


def test_withdraw_more_than_balance_is_rejected(client):
    signup_and_login(client)
    open_account(client)
    acc_id = all_accounts()[0].id
    client.post(f"/accounts/{acc_id}/deposit", data={"amount": "500", "category": "salary"})
    client.post(f"/accounts/{acc_id}/withdraw", data={"amount": "900", "category": "food"})
    assert balance_of(acc_id) == 500.0


def test_negative_deposit_is_rejected(client):
    signup_and_login(client)
    open_account(client)
    acc_id = all_accounts()[0].id
    client.post(f"/accounts/{acc_id}/deposit", data={"amount": "-50", "category": "x"})
    assert balance_of(acc_id) == 0.0


def test_blank_category_defaults_to_general(client):
    signup_and_login(client)
    open_account(client)
    acc_id = all_accounts()[0].id
    client.post(f"/accounts/{acc_id}/deposit", data={"amount": "100", "category": ""})
    db.session.expire_all()
    assert Transaction.query.first().category == "general"


def test_transfer_moves_money_and_records_both_sides(client):
    signup_and_login(client)
    open_account(client, "savings")
    open_account(client, "current")
    first, second = [a.id for a in all_accounts()]
    client.post(f"/accounts/{first}/deposit", data={"amount": "1000", "category": "salary"})
    client.post("/transfer", data={"from_account_id": first, "to_account_id": second, "amount": "300"})
    assert balance_of(first) == 700.0
    assert balance_of(second) == 300.0
    db.session.expire_all()
    assert sorted(t.type for t in Transaction.query.all()) == ["deposit", "transfer_in", "transfer_out"]


def test_transfer_with_insufficient_funds_is_rejected(client):
    signup_and_login(client)
    open_account(client, "savings")
    open_account(client, "current")
    first, second = [a.id for a in all_accounts()]
    client.post(f"/accounts/{first}/deposit", data={"amount": "100", "category": "salary"})
    client.post("/transfer", data={"from_account_id": first, "to_account_id": second, "amount": "500"})
    assert balance_of(first) == 100.0
    assert balance_of(second) == 0.0


def test_cannot_deposit_into_someone_elses_account(client):
    signup_and_login(client, "owner@example.com", "Owner")
    open_account(client)
    acc_id = all_accounts()[0].id
    intruder = app.test_client()
    signup_and_login(intruder, "intruder@example.com", "Intruder")
    intruder.post(f"/accounts/{acc_id}/deposit", data={"amount": "999", "category": "x"})
    assert balance_of(acc_id) == 0.0


def test_loan_application_starts_as_pending(client):
    signup_and_login(client)
    client.post("/loans", data=LOAN)
    db.session.expire_all()
    loan = Loan.query.first()
    assert loan.status == "pending"
    assert loan.amount == 50000.0


def test_customer_cannot_approve_own_loan(client):
    signup_and_login(client)
    client.post("/loans", data=LOAN)
    loan_id = first_id(Loan)
    response = client.post(f"/admin/loans/{loan_id}/status", data={"status": "approved"})
    assert response.status_code == 302
    db.session.expire_all()
    assert db.session.get(Loan, loan_id).status == "pending"


def test_admin_can_approve_loan(client):
    signup_and_login(client, "cust@example.com", "Cust")
    open_account(client)
    acc_id = all_accounts()[0].id
    client.post("/loans", data=LOAN)
    loan_id = first_id(Loan)
    admin_user = User(email="admin@example.com", role="admin")
    admin_user.set_password("adminpw1")
    db.session.add(admin_user)
    db.session.commit()
    admin_client = app.test_client()
    admin_client.post("/login", data={"email": "admin@example.com", "password": "adminpw1"})
    admin_client.post(f"/admin/loans/{loan_id}/status", data={"status": "approved"})
    db.session.expire_all()
    assert db.session.get(Loan, loan_id).status == "approved"
    assert balance_of(acc_id) == 50000.0


def test_repaying_full_amount_closes_loan(client):
    signup_and_login(client)
    open_account(client)
    acc_id = all_accounts()[0].id
    client.post(f"/accounts/{acc_id}/deposit", data={"amount": "5000", "category": "salary"})
    client.post("/loans", data={"amount": "1000", "interest_rate": "8", "term_months": "12"})
    loan_id = first_id(Loan)
    db.session.get(Loan, loan_id).status = "approved"
    db.session.commit()
    client.post(f"/loans/{loan_id}/repay", data={"amount": "1000"})
    db.session.expire_all()
    assert db.session.get(Loan, loan_id).status == "closed"
    assert balance_of(acc_id) == 4000.0


def test_credit_card_is_issued_with_limit(client):
    signup_and_login(client)
    client.post("/credit-cards", data={"credit_limit": "20000"})
    db.session.expire_all()
    card = CreditCard.query.first()
    assert card.credit_limit == 20000.0
    assert card.balance == 0.0
    assert len(card.card_number) == 16


def test_card_charge_over_limit_is_rejected(client):
    signup_and_login(client)
    client.post("/credit-cards", data={"credit_limit": "1000"})
    card_id = first_id(CreditCard)
    client.post(f"/credit-cards/{card_id}/charge", data={"amount": "800"})
    client.post(f"/credit-cards/{card_id}/charge", data={"amount": "500"})
    db.session.expire_all()
    assert db.session.get(CreditCard, card_id).balance == 800.0


def test_card_payment_reduces_card_and_account_balance(client):
    signup_and_login(client)
    open_account(client)
    acc_id = all_accounts()[0].id
    client.post(f"/accounts/{acc_id}/deposit", data={"amount": "1000", "category": "salary"})
    client.post("/credit-cards", data={"credit_limit": "1000"})
    card_id = first_id(CreditCard)
    client.post(f"/credit-cards/{card_id}/charge", data={"amount": "600"})
    client.post(f"/credit-cards/{card_id}/pay", data={"amount": "250"})
    db.session.expire_all()
    assert db.session.get(CreditCard, card_id).balance == 350.0
    assert balance_of(acc_id) == 750.0


    # ======================================================
# VALIDATION TESTS
# ======================================================

def test_negative_loan_amount_is_rejected(client):
    signup_and_login(client)
    client.post("/loans", data={"amount": "-100", "interest_rate": "8", "term_months": "12"})
    db.session.expire_all()
    assert Loan.query.count() == 0


def test_loan_with_zero_term_is_rejected(client):
    signup_and_login(client)
    client.post("/loans", data={"amount": "1000", "interest_rate": "8", "term_months": "0"})
    db.session.expire_all()
    assert Loan.query.count() == 0


def test_cannot_repay_more_than_remaining(client):
    signup_and_login(client)
    client.post("/loans", data={"amount": "1000", "interest_rate": "8", "term_months": "12"})
    loan_id = first_id(Loan)
    db.session.get(Loan, loan_id).status = "approved"
    db.session.commit()
    client.post(f"/loans/{loan_id}/repay", data={"amount": "1500"})
    db.session.expire_all()
    assert db.session.get(Loan, loan_id).amount_repaid == 0.0


def test_negative_repayment_is_rejected(client):
    signup_and_login(client)
    client.post("/loans", data={"amount": "1000", "interest_rate": "8", "term_months": "12"})
    loan_id = first_id(Loan)
    db.session.get(Loan, loan_id).status = "approved"
    db.session.commit()
    client.post(f"/loans/{loan_id}/repay", data={"amount": "-200"})
    db.session.expire_all()
    assert db.session.get(Loan, loan_id).amount_repaid == 0.0


def test_card_with_non_positive_limit_is_rejected(client):
    signup_and_login(client)
    client.post("/credit-cards", data={"credit_limit": "-500"})
    db.session.expire_all()
    assert CreditCard.query.count() == 0


def test_negative_card_charge_is_rejected(client):
    signup_and_login(client)
    client.post("/credit-cards", data={"credit_limit": "1000"})
    card_id = first_id(CreditCard)
    client.post(f"/credit-cards/{card_id}/charge", data={"amount": "-300"})
    db.session.expire_all()
    assert db.session.get(CreditCard, card_id).balance == 0.0


def test_negative_card_payment_is_rejected(client):
    signup_and_login(client)
    client.post("/credit-cards", data={"credit_limit": "1000"})
    card_id = first_id(CreditCard)
    client.post(f"/credit-cards/{card_id}/charge", data={"amount": "200"})
    client.post(f"/credit-cards/{card_id}/pay", data={"amount": "-100"})
    db.session.expire_all()
    assert db.session.get(CreditCard, card_id).balance == 200.0

    # ======================================================
# MONEY-FLOW TESTS: loans and cards move real money
# ======================================================

def make_admin_client():
    admin_user = User(email="boss@example.com", role="admin")
    admin_user.set_password("adminpw1")
    db.session.add(admin_user)
    db.session.commit()
    admin_client = app.test_client()
    admin_client.post("/login", data={"email": "boss@example.com", "password": "adminpw1"})
    return admin_client


def funded_account(client, amount="1000"):
    open_account(client)
    acc_id = all_accounts()[0].id
    client.post(f"/accounts/{acc_id}/deposit", data={"amount": amount, "category": "salary"})
    return acc_id


def approved_loan(client, amount="1000"):
    client.post("/loans", data={"amount": amount, "interest_rate": "8", "term_months": "12"})
    loan_id = first_id(Loan)
    db.session.get(Loan, loan_id).status = "approved"
    db.session.commit()
    return loan_id


def test_approving_a_loan_credits_the_account(client):
    signup_and_login(client, "c1@example.com", "C1")
    acc_id = funded_account(client, "100")
    client.post("/loans", data=LOAN)
    loan_id = first_id(Loan)
    admin = make_admin_client()
    admin.post(f"/admin/loans/{loan_id}/status", data={"status": "approved"})
    assert balance_of(acc_id) == 50100.0
    db.session.expire_all()
    assert Transaction.query.filter_by(type="loan_disbursal").count() == 1


def test_approving_twice_credits_only_once(client):
    signup_and_login(client, "c2@example.com", "C2")
    acc_id = funded_account(client, "100")
    client.post("/loans", data=LOAN)
    loan_id = first_id(Loan)
    admin = make_admin_client()
    admin.post(f"/admin/loans/{loan_id}/status", data={"status": "approved"})
    admin.post(f"/admin/loans/{loan_id}/status", data={"status": "approved"})
    assert balance_of(acc_id) == 50100.0


def test_rejecting_a_loan_credits_nothing(client):
    signup_and_login(client, "c3@example.com", "C3")
    acc_id = funded_account(client, "100")
    client.post("/loans", data=LOAN)
    loan_id = first_id(Loan)
    admin = make_admin_client()
    admin.post(f"/admin/loans/{loan_id}/status", data={"status": "rejected"})
    assert balance_of(acc_id) == 100.0
    db.session.expire_all()
    assert db.session.get(Loan, loan_id).status == "rejected"


def test_loan_cannot_be_approved_without_an_account(client):
    signup_and_login(client, "c4@example.com", "C4")
    client.post("/loans", data=LOAN)
    loan_id = first_id(Loan)
    admin = make_admin_client()
    admin.post(f"/admin/loans/{loan_id}/status", data={"status": "approved"})
    db.session.expire_all()
    assert db.session.get(Loan, loan_id).status == "pending"


def test_invalid_loan_status_is_ignored(client):
    signup_and_login(client, "c5@example.com", "C5")
    funded_account(client, "100")
    client.post("/loans", data=LOAN)
    loan_id = first_id(Loan)
    admin = make_admin_client()
    admin.post(f"/admin/loans/{loan_id}/status", data={"status": "closed"})
    db.session.expire_all()
    assert db.session.get(Loan, loan_id).status == "pending"


def test_repayment_with_insufficient_funds_is_rejected(client):
    signup_and_login(client)
    acc_id = funded_account(client, "100")
    loan_id = approved_loan(client)
    client.post(f"/loans/{loan_id}/repay", data={"amount": "500"})
    db.session.expire_all()
    assert db.session.get(Loan, loan_id).amount_repaid == 0.0
    assert balance_of(acc_id) == 100.0


def test_repayment_without_an_account_is_rejected(client):
    signup_and_login(client)
    loan_id = approved_loan(client)
    client.post(f"/loans/{loan_id}/repay", data={"amount": "500"})
    db.session.expire_all()
    assert db.session.get(Loan, loan_id).amount_repaid == 0.0


def test_card_payment_with_insufficient_funds_is_rejected(client):
    signup_and_login(client)
    acc_id = funded_account(client, "100")
    client.post("/credit-cards", data={"credit_limit": "1000"})
    card_id = first_id(CreditCard)
    client.post(f"/credit-cards/{card_id}/charge", data={"amount": "600"})
    client.post(f"/credit-cards/{card_id}/pay", data={"amount": "300"})
    db.session.expire_all()
    assert db.session.get(CreditCard, card_id).balance == 600.0
    assert balance_of(acc_id) == 100.0


def test_card_overpayment_only_takes_what_is_owed(client):
    signup_and_login(client)
    acc_id = funded_account(client, "1000")
    client.post("/credit-cards", data={"credit_limit": "1000"})
    card_id = first_id(CreditCard)
    client.post(f"/credit-cards/{card_id}/charge", data={"amount": "200"})
    client.post(f"/credit-cards/{card_id}/pay", data={"amount": "500"})
    db.session.expire_all()
    assert db.session.get(CreditCard, card_id).balance == 0.0
    assert balance_of(acc_id) == 800.0


def test_dashboard_has_account_transfer_dropdowns_and_currency_choices(client):
    signup_and_login(client)
    open_account(client, "savings")
    open_account(client, "current")

    response = client.get("/dashboard")
    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'id="transfer-from"' in page
    assert 'id="transfer-to"' in page
    assert 'name="to_account_id"' in page
    assert 'type="number" name="to_account_id"' not in page
    assert 'id="fx-from"' in page
    assert 'id="fx-to"' in page
    assert 'value="INR"' in page and 'value="USD"' in page and 'value="JPY"' in page
    assert "/api/v1/integrations/currency?" in page
    assert "Stripe Checkout" in page
    assert "KYC" not in page

    admin = User(email="dashboard-admin@example.com", role="admin")
    admin.set_password("adminpw1")
    db.session.add(admin)
    db.session.commit()
    admin_client = app.test_client()
    admin_client.post("/login", data={"email": admin.email, "password": "adminpw1"})
    admin_page = admin_client.get("/admin").get_data(as_text=True)
    assert "KYC" not in admin_page


def test_transfer_rejects_another_customers_account(client):
    signup_and_login(client, "first@example.com", "First")
    open_account(client, "savings")
    own_account = all_accounts()[0]

    other_client = app.test_client()
    signup_and_login(other_client, "second@example.com", "Second")
    open_account(other_client, "savings")
    other_account_id = all_accounts()[-1].id

    client.post("/accounts/transfer", data={
        "from_account_id": own_account.id,
        "to_account_id": other_account_id,
        "amount": "10",
    })
    assert balance_of(own_account.id) == 0
    assert balance_of(other_account_id) == 0


def test_stripe_deposit_requires_configuration(client, monkeypatch):
    signup_and_login(client)
    account_id = funded_account(client)
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)

    response = client.post("/payments/stripe-deposit", data={
        "account_id": account_id, "amount": "25",
    })
    assert response.status_code == 302
    assert balance_of(account_id) == 1000
    db.session.expire_all()
    assert StripeDeposit.query.count() == 0


def test_stripe_deposit_verifies_payment_and_credits_once(client, monkeypatch):
    import stripe
    from types import SimpleNamespace

    signup_and_login(client)
    account_id = funded_account(client)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_example")
    checkout_state = SimpleNamespace(
        id="cs_test_deposit",
        url="https://checkout.stripe.test/session",
        status="open",
        payment_status="unpaid",
        mode="payment",
        amount_total=2500,
        currency="inr",
        metadata={"customer_id": str(current_customer_id()), "account_id": str(account_id)},
    )
    stripe_session = SimpleNamespace(
        create=lambda **kwargs: checkout_state,
        retrieve=lambda session_id, **kwargs: checkout_state,
    )
    monkeypatch.setattr(stripe, "checkout", SimpleNamespace(Session=stripe_session))

    response = client.post("/payments/stripe-deposit", data={
        "account_id": account_id, "amount": "25",
    })
    assert response.status_code == 303
    assert response.headers["Location"] == checkout_state.url
    assert balance_of(account_id) == 1000

    client.get("/payments/stripe-success?session_id=cs_test_deposit")
    assert balance_of(account_id) == 1000

    checkout_state.status = "complete"
    checkout_state.payment_status = "paid"
    client.get("/payments/stripe-success?session_id=cs_test_deposit")
    assert balance_of(account_id) == 1025
    client.get("/payments/stripe-success?session_id=cs_test_deposit")
    assert balance_of(account_id) == 1025
    db.session.expire_all()
    assert Transaction.query.filter_by(category="stripe_gateway").count() == 1


def test_method_not_allowed_flashes_and_redirects_to_dashboard(client):
    signup_and_login(client)

    response = client.get("/accounts/new")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")
    with client.session_transaction() as session:
        assert any(
            "not available with this request method" in message
            for _, message in session.get("_flashes", [])
        )


def current_customer_id():
    return User.query.filter_by(email="user@example.com").first().customer.id