# SPDX-License-Identifier: AGPL-3.0-only
"""Access review: propose taking rights away, or granting them, by verification work.

Two directions over the same count (owner, 2026-10-27):

* **revoke**: a person registered more than ``months`` ago keeps a right for an
  institution, in one module, only after verifying at least ``min_units`` units
  on that institution's locations. Otherwise the right is proposed for removal
  for that institution and module only.
* **grant**: a person who verified at least ``min_units`` units on an
  institution's locations and lacks the right there is proposed to get it. Age
  does not matter: the work is the evidence.

Units per module:
  * camera traps: a series, counted once if the person identified any photo of
    it (``identifications`` -> ``photos`` -> ``observations``). Archived series
    count: archival deletes files, not identification rows.
  * PAM: a verified segment (``segment_verifications`` -> ``segments`` ->
    ``recordings.location_id``). Legacy segments without a recording have no
    location and are not counted.

Rights:
  * ``export``: ``can_export_<module>``. Granting needs access in that module
    already (export implies access); it is stored even without an export role,
    but then has no effect, which the item says (``note``).
  * ``access``: ``can_view_<module>``. Revoking also switches off export and
    manage of that module (both require access) and deletes a row that then
    grants nothing. Granting is how someone who verified a park's public
    locations gets into its non-public ones.

Never proposed: admins, excluded usernames; for revoke also accounts younger
than ``months`` or without a registration date, and anyone who manages that
institution (in either module): the owner ruled that a park's manager keeps
every right in that park.

``propose`` only reads. ``apply`` re-runs ``propose`` with the same parameters
and changes only pairs that are still proposed, so a stale or crafted form
cannot change anything the rule does not cover.
"""
from datetime import datetime, timedelta

from sqlalchemy import text

from app.extensions import db

MODULES = ('ct', 'pam')
RIGHTS = ('export', 'access')
DIRECTIONS = ('revoke', 'grant')

DEFAULT_MONTHS = 6
DEFAULT_MIN_UNITS = 50
#: Average month length, so 6 months = 183 days as in the first manual run.
DAYS_PER_MONTH = 30.44

#: Roles that make each grant effective (role hierarchy included via has_role).
EXPORT_ROLES = ('analyst', 'manager', 'admin')
VERIFIER_ROLE = {'ct': 'ct_verifier', 'pam': 'pam_verifier'}

_UNITS_SQL = {
    'ct': """
        SELECT i.user_id, li.institution_id, COUNT(DISTINCT p.observation_id)
        FROM identifications i
        JOIN photos p ON p.id = i.photo_id
        JOIN observations o ON o.id = p.observation_id
        JOIN location_institutions li ON li.location_id = o.location_id
        GROUP BY i.user_id, li.institution_id
    """,
    'pam': """
        SELECT sv.user_id, li.institution_id, COUNT(DISTINCT sv.segment_id)
        FROM segment_verifications sv
        JOIN segments s ON s.id = sv.segment_id
        JOIN recordings r ON r.recording_id = s.recording_id
        JOIN location_institutions li ON li.location_id = r.location_id
        GROUP BY sv.user_id, li.institution_id
    """,
}


def _engine(module):
    if module == 'ct':
        from app.camera_traps.database import get_ct_engine
        return get_ct_engine()
    from app.pam.utils import get_pam_engine
    return get_pam_engine()


def verified_units(module):
    """{(user_id, institution_id): units verified there} for one module."""
    with _engine(module).connect() as conn:
        rows = conn.execute(text(_UNITS_SQL[module])).fetchall()
    return {(u, inst): n for u, inst, n in rows}


def _as_datetime(value):
    """Raw SQL gives a datetime on PostgreSQL and a string on SQLite."""
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _flag(module, right):
    return f"can_{'export' if right == 'export' else 'view'}_{module}"


def _item(user, inst, units):
    return {'user_id': user.id, 'username': user.username,
            'name': ' '.join(x for x in (user.first_name, user.last_name) if x),
            'created_at': _as_datetime(user.created_at),
            'institution_id': inst.id, 'institution': inst.name_uk,
            'code': inst.code, 'units': units, 'note': None}


def _check(module, right, direction):
    if module not in MODULES or right not in RIGHTS or direction not in DIRECTIONS:
        raise ValueError('unknown module, right or direction')


def propose(module, right, months=DEFAULT_MONTHS, min_units=DEFAULT_MIN_UNITS,
            exclude=(), now=None, counts=None, direction='revoke'):
    """What the rule would change, plus what it leaves alone and why.

    Returns:
        dict with ``items`` (proposed changes, sorted by person then
        institution) and ``skipped`` (reason -> items). Each item: user_id,
        username, name, created_at, institution_id, institution, code, units,
        note (a warning to show, or None).
    """
    from app.models import Institution, User, UserInstitution

    _check(module, right, direction)
    now = now or datetime.utcnow()
    cutoff = now - timedelta(days=round(months * DAYS_PER_MONTH))
    counts = verified_units(module) if counts is None else counts
    flag = _flag(module, right)
    exclude = {e.strip() for e in exclude if e and e.strip()}
    skipped = {'excluded': [], 'admin': [], 'manages': [], 'no_date': [],
               'too_young': [], 'enough': [], 'too_few': [], 'no_access': []}
    items = []

    links = {(l.user_id, l.institution_id): l for l in UserInstitution.query.all()}
    users, insts = {}, {}

    def user(uid):
        if uid not in users:
            users[uid] = User.query.get(uid)
        return users[uid]

    def inst(iid):
        if iid not in insts:
            insts[iid] = Institution.query.get(iid)
        return insts[iid]

    if direction == 'revoke':
        candidates = [key for key, l in links.items() if getattr(l, flag)]
    elif right == 'export':
        # Export can only follow access in the same module.
        candidates = [key for key, l in links.items()
                      if getattr(l, f'can_view_{module}') and not getattr(l, flag)]
        candidates += [key for key in counts
                       if counts[key] >= min_units and key not in links]
    else:
        candidates = [key for key, n in counts.items()
                      if n >= min_units and not (key in links and getattr(links[key], flag))]

    for uid, iid in sorted(set(candidates)):
        u, i = user(uid), inst(iid)
        if u is None or i is None:
            continue
        units = counts.get((uid, iid), 0)
        it = _item(u, i, units)
        link = links.get((uid, iid))
        if u.username in exclude:
            skipped['excluded'].append(it)
        elif u.has_role('admin'):
            skipped['admin'].append(it)
        elif direction == 'grant':
            if units < min_units:
                skipped['too_few'].append(it)
            elif right == 'export' and not (link and getattr(link, f'can_view_{module}')):
                skipped['no_access'].append(it)
            else:
                if right == 'export' and not u.has_role(*EXPORT_ROLES):
                    it['note'] = 'no_export_role'
                elif right == 'access' and not u.has_role(VERIFIER_ROLE[module]):
                    it['note'] = 'no_verifier_role'
                items.append(it)
        elif (link and (link.can_manage_ct or link.can_manage_pam)
              and u.has_role('manager')):
            # Owner: a manager of the institution keeps every right there.
            skipped['manages'].append(it)
        elif it['created_at'] is None:
            skipped['no_date'].append(it)
        elif it['created_at'] > cutoff:
            skipped['too_young'].append(it)
        elif units >= min_units:
            skipped['enough'].append(it)
        else:
            items.append(it)

    items.sort(key=lambda x: (x['username'].casefold(), x['institution']))
    return {'module': module, 'right': right, 'direction': direction,
            'months': months, 'min_units': min_units, 'cutoff': cutoff,
            'exclude': sorted(exclude), 'items': items, 'skipped': skipped}


def apply(module, right, pairs, months=DEFAULT_MONTHS, min_units=DEFAULT_MIN_UNITS,
          exclude=(), counts=None, direction='revoke', now=None):
    """Change the right for the selected ``(user_id, institution_id)`` pairs.

    Only pairs that ``propose`` still returns are touched. No commit; the
    caller commits. Returns the items actually changed.
    """
    from app.models import UserInstitution

    proposal = propose(module, right, months, min_units, exclude, now=now,
                       counts=counts, direction=direction)
    allowed = {(i['user_id'], i['institution_id']): i for i in proposal['items']}
    chosen = [allowed[p] for p in dict.fromkeys(pairs) if p in allowed]
    flag = _flag(module, right)
    for item in chosen:
        params = {'u': item['user_id'], 'i': item['institution_id']}
        if direction == 'grant':
            link = UserInstitution.query.filter_by(
                user_id=item['user_id'], institution_id=item['institution_id']).first()
            if link is None:
                db.session.add(UserInstitution(
                    user_id=item['user_id'], institution_id=item['institution_id'],
                    **{flag: True}))
            else:
                setattr(link, flag, True)
        elif right == 'export':
            db.session.execute(text(f"""
                UPDATE user_institutions SET {flag} = FALSE
                WHERE user_id = :u AND institution_id = :i"""), params)
        else:
            db.session.execute(text(f"""
                UPDATE user_institutions
                SET can_view_{module} = FALSE, can_export_{module} = FALSE,
                    can_manage_{module} = FALSE
                WHERE user_id = :u AND institution_id = :i"""), params)
            # The table is sparse: a row granting nothing is "no access".
            db.session.execute(text("""
                DELETE FROM user_institutions
                WHERE user_id = :u AND institution_id = :i
                  AND NOT can_view_ct AND NOT can_view_pam"""), params)
    db.session.flush()
    return chosen
