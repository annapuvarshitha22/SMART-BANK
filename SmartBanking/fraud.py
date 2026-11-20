from datetime import datetime, timedelta

from models import db, Transaction, FraudAlert

LARGE_AMOUNT_THRESHOLD = 100000.0
RAPID_WINDOW_MINUTES = 5
RAPID_COUNT = 4


def check_transaction_for_fraud(transaction):
    """Persist and return rule-based fraud alerts for a committed transaction."""
    reasons = []

    # This threshold rule is independent of transaction history.
    if transaction.amount >= LARGE_AMOUNT_THRESHOLD:
        reasons.append(("Unusually large transaction amount", "high"))

    window_start = datetime.utcnow() - timedelta(minutes=RAPID_WINDOW_MINUTES)
    recent_count = Transaction.query.filter(
        Transaction.account_id == transaction.account_id,
        Transaction.timestamp >= window_start,
    ).count()
    if recent_count >= RAPID_COUNT:
        reasons.append(("Multiple transactions in a short time window", "medium"))

    hour = transaction.timestamp.hour if transaction.timestamp else datetime.utcnow().hour
    if hour < 5 or hour > 23:
        reasons.append(("Transaction at an unusual hour", "low"))

    alerts = [
        FraudAlert(transaction_id=transaction.id, reason=reason, severity=severity)
        for reason, severity in reasons
    ]
    db.session.add_all(alerts)

    if alerts:
        db.session.commit()
    return alerts