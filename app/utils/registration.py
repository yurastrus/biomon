# SPDX-License-Identifier: AGPL-3.0-only
"""Self-service registration logic, kept out of the HTTP layer.

Ownership of the two gates involved:

* **email confirmation** — decides whether the account can log in at all
  (``User.is_active``); fully automatic.
* **verification rights** — decides what the account may do; granted by an
  administrator through :class:`app.models.VerificationRequest`.

Both are deliberately separate: a confirmed address is not a vetted verifier.
"""
from datetime import datetime, timedelta

from flask import current_app

from app.extensions import db, bcrypt
from app.models import User, Role, VerificationRequest

#: Role every account gets on registration — read-only access to public data.
BASE_ROLE = 'viewer'


def get_or_create_role(name):
    """Return the Role named ``name``, creating it if the deployment lacks it.

    Role rows are normally managed in the admin panel, but the handful of names
    the registration flow depends on are system constants: a fresh deployment
    must not be able to break approval by simply not having created them yet.
    """
    role = Role.query.filter_by(name=name).first()
    if role is None:
        current_app.logger.warning("Role %r missing — creating it", name)
        role = Role(name=name)
        db.session.add(role)
        db.session.flush()
    return role


def username_taken(username):
    return db.session.query(
        User.query.filter(db.func.lower(User.username) == username.lower()).exists()
    ).scalar()


def email_taken(email):
    return db.session.query(
        User.query.filter(db.func.lower(User.email) == email.lower()).exists()
    ).scalar()


def create_self_registered_user(*, username, email, password, first_name, last_name,
                                modules, locale='uk'):
    """Create an inactive account plus one pending request per chosen module.

    The caller commits. The account is created with the ``viewer`` role and **no
    institutions**, which is what limits a future verifier to public locations.

    Args:
        modules: iterable of :data:`VerificationRequest.MODULES` values.

    Returns:
        The new :class:`User`.
    """
    modules = [m for m in modules if m in VerificationRequest.MODULES]
    if not modules:
        raise ValueError("at least one module must be requested")

    user = User(
        username=username,
        password_hash=bcrypt.generate_password_hash(password).decode('utf-8'),
        email=email,
        first_name=first_name or None,
        last_name=last_name or None,
        is_active=False,          # activated by the confirmation link
        self_registered=True,
        locale=locale,
    )
    user.roles.append(get_or_create_role(BASE_ROLE))
    db.session.add(user)
    db.session.flush()            # need user.id for the requests below

    for module in modules:
        db.session.add(VerificationRequest(user_id=user.id, module=module))

    return user


def confirm_email(user):
    """Activate an account whose address has just been proven.

    Idempotent: confirming twice is not an error, and it never re-activates an
    account an administrator has deliberately disabled after confirmation.

    Returns:
        bool: True if this call performed the confirmation, False if the address
        was already confirmed.
    """
    if user.is_email_confirmed:
        return False
    user.email_confirmed_at = datetime.utcnow()
    user.is_active = True
    return True


def purge_unconfirmed_users(max_age_days=7):
    """Delete self-registered accounts that never confirmed their address.

    Keeps abandoned and bot signups from accumulating (and keeps their usernames
    and email addresses free). Only ever touches rows that are all three of:
    self-registered, unconfirmed, and older than ``max_age_days``.

    Returns:
        int: number of accounts deleted.
    """
    cutoff = datetime.utcnow() - timedelta(days=max_age_days)
    stale = User.query.filter(
        User.self_registered.is_(True),
        User.email_confirmed_at.is_(None),
        User.created_at < cutoff,
    ).all()
    for user in stale:
        # Requests cascade with the user (relationship + FK ON DELETE CASCADE).
        db.session.delete(user)
    if stale:
        db.session.commit()
        current_app.logger.info("Purged %d unconfirmed account(s)", len(stale))
    return len(stale)
