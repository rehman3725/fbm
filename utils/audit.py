from models import db, AuditLog


def audit_log(user, action, details=None):
    """
    Single-store audit logging.

    Historical multi-tenant versions of this app included tenant_id context; in
    single-store mode we keep the same audit_log call sites but only persist
    user/action/details.
    """
    try:
        db.session.add(
            AuditLog(
                user_id=(getattr(user, "id", None) if user else None),
                action=action,
                details=details,
            )
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
