import os
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from app import app
from models import db, User, Customer, Account


def setup_function():
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
                      JWT_SECRET_KEY="test-secret")


def test_api_health():
    with app.app_context():
        db.create_all()
        client = app.test_client()
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.get_json()["success"] is True
        db.drop_all()


def test_jwt_login_and_accounts():
    with app.app_context():
        db.create_all()
        user = User(email="api@example.com", role="customer")
        user.set_password("strong-test-password")
        db.session.add(user)
        db.session.flush()
        customer = Customer(user_id=user.id, full_name="API Test")
        db.session.add(customer)
        db.session.flush()
        db.session.add(Account(customer_id=customer.id, account_type="savings", balance=1250))
        db.session.commit()
        client = app.test_client()
        login = client.post("/api/v1/auth/login",
                            json={"email": "api@example.com", "password": "strong-test-password"})
        assert login.status_code == 200
        token = login.get_json()["access_token"]
        accounts = client.get("/api/v1/accounts",
                              headers={"Authorization": f"Bearer {token}"})
        assert accounts.status_code == 200
        assert accounts.get_json()["accounts"][0]["balance"] == 1250
        db.session.remove()
        db.drop_all()


def test_protected_endpoint_rejects_missing_token():
    with app.app_context():
        db.create_all()
        response = app.test_client().get("/api/v1/accounts")
        assert response.status_code == 401
        db.drop_all()


def test_removed_kyc_api_endpoints_return_not_found():
    with app.app_context():
        db.create_all()
        client = app.test_client()
        for path in ("/api/v1/kyc", "/api/v1/kyc/submit", "/api/v1/admin/kyc"):
            assert client.get(path).status_code == 404
        assert client.post("/api/v1/kyc/submit", json={}).status_code == 404
        assert client.post("/api/v1/admin/kyc/1/status", json={}).status_code == 404
        db.session.remove()
        db.drop_all()
