# SPDX-License-Identifier: AGPL-3.0-only
"""Registry of the per-user email notification preferences.

Why a registry rather than checking ``user.notify_ct_pending`` directly: the
same list has to be rendered on the profile page, rendered again on the admin
user form, and read back from both POST bodies. Keeping the definition in one
place means the second notification type (PAM, once its digest exists) is a
migration plus one entry here — no template or route changes.

Adding a notification type
--------------------------
1. Add a ``notify_<key>`` Boolean column to ``User`` (default True, so existing
   accounts stay subscribed) and write the migration.
2. Append a :class:`NotificationPref` below.
3. In the sender, skip users for which ``is_enabled(user, '<key>')`` is False,
   and put the opt-out instructions in the message body.

The PAM entry is intentionally absent: there is no PAM digest yet, and a
checkbox that switches off something nobody sends is worse than no checkbox.
"""
from collections import namedtuple

from flask_babel import lazy_gettext as _l

#: key    — stable identifier used in form fields and by senders
#: column — the Boolean attribute on User
#: label  — checkbox label
#: hint   — one line under the label explaining when the email arrives
NotificationPref = namedtuple('NotificationPref', 'key column label hint')

NOTIFICATION_PREFS = (
    NotificationPref(
        key='ct_pending',
        column='notify_ct_pending',
        label=_l('Фотопастки: нагадування про серії, що чекають на визначення'),
        hint=_l('Лист раз на тиждень, і лише якщо на вас чекає щонайменше '
                '10 серій. Знята галочка вимикає його повністю.'),
    ),
    # NotificationPref(key='pam_pending', column='notify_pam_pending', ...)
    #   — add together with the PAM digest, see the module docstring.
)

#: Field name prefix for the checkboxes in both forms.
FIELD_PREFIX = 'notify_'


def is_enabled(user, key):
    """Return True if ``user`` wants the notification identified by ``key``.

    Unknown keys and users loaded from a schema without the column both answer
    True: a preference nobody has ever set means "not opted out".
    """
    for pref in NOTIFICATION_PREFS:
        if pref.key == key:
            return bool(getattr(user, pref.column, True))
    return True


def apply_form(user, form_data):
    """Write the checkbox state from a submitted form onto ``user``.

    ``form_data`` is a ``request.form``-like mapping. An unchecked HTML checkbox
    sends nothing at all, so absence means False — which is only safe because
    every caller submits the whole notification section at once.

    Returns:
        list[str]: keys whose value actually changed (for the audit log).
    """
    changed = []
    for pref in NOTIFICATION_PREFS:
        new_value = form_data.get(FIELD_PREFIX + pref.key) is not None
        if bool(getattr(user, pref.column)) != new_value:
            setattr(user, pref.column, new_value)
            changed.append(pref.key)
    return changed
