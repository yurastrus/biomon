# SPDX-License-Identifier: AGPL-3.0-only
from flask import render_template, g, request, redirect, url_for, flash, current_app
from flask_login import login_required, current_user
from flask_babel import gettext as _

from app.extensions import db
from app.models import (User, Institution, Role, ContactSubmission,
                        VerificationRequest)
from app.utils.decorators import role_required
from app.utils.utils import build_institution_groups
from app.admin.forms import UserCreateForm, UserEditForm, InstitutionForm, RoleForm
from app.admin.services import (UserService, InstitutionService, RoleService,
                                VerificationRequestService)
from app.utils.emails import send_decision_email, send_rights_granted_email
from app.utils import notification_prefs as notif_prefs
from . import admin_bp


#: Shared with the public registration form — see app/utils/utils.py.
_build_inst_groups = build_institution_groups


# ===========================================================================
# Admin panel home
# ===========================================================================

@admin_bp.route('/')
@login_required
@role_required('admin', 'manager')
def home():
    can_decide_requests = VerificationRequestService.can_decide(current_user)
    viewer = None if current_user.has_role('admin') else current_user
    pending_requests = (VerificationRequestService.pending_count(viewer=viewer)
                        if can_decide_requests else 0)
    return render_template('admin_home.html',
                           pending_requests=pending_requests,
                           can_decide_requests=can_decide_requests)


# ===========================================================================
# User management
# ===========================================================================

@admin_bp.route('/users')
@login_required
@role_required('admin', 'manager')
def user_list():
    if current_user.has_role('admin'):
        users = User.query.order_by(User.id.desc()).all()
    else:
        # Managers see only users they created
        users = User.query.filter_by(created_by_id=current_user.id).order_by(User.id.desc()).all()
    return render_template('admin_users_list.html', users=users)


@admin_bp.route('/users/add', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'manager')
def add_user():
    available_institutions = UserService.get_available_institutions(current_user)
    available_roles        = UserService.get_available_roles(current_user)

    form = UserCreateForm()

    # Capture checkbox selections before validation so they survive a re-render on error.
    # On GET, all sets are empty (no pre-selection).
    if request.method == 'POST':
        selected_inst_ids   = set(request.form.getlist('institutions'))
        selected_export_ids = set(request.form.getlist('can_export'))
        selected_role_ids   = set(request.form.getlist('roles'))
    else:
        selected_inst_ids   = set()
        selected_export_ids = set()
        selected_role_ids   = set()

    if form.validate_on_submit():
        try:
            UserService.create_user(
                creator=current_user,
                username=form.username.data,
                password=form.password.data,
                email=form.email.data,
                phone=form.phone.data,
                first_name=form.first_name.data,
                last_name=form.last_name.data,
                selected_inst_ids=list(selected_inst_ids),
                can_export_ids=selected_export_ids,
                selected_role_ids=list(selected_role_ids),
            )
            db.session.commit()
            flash(f'Користувача {form.username.data} успішно створено!', 'success')
            return redirect(url_for('admin.user_list', lang_code=g.lang_code))
        except Exception as e:
            db.session.rollback()
            flash(f'Помилка при збереженні: {str(e)}', 'danger')

    if form.errors:
        for field_errors in form.errors.values():
            for err in field_errors:
                flash(err, 'danger')

    inst_groups = _build_inst_groups(available_institutions, g.lang_code)

    return render_template('admin_user_form.html',
                           form=form,
                           inst_groups=inst_groups,
                           roles=available_roles,
                           selected_inst_ids=selected_inst_ids,
                           selected_export_ids=selected_export_ids,
                           selected_role_ids=selected_role_ids,
                           notification_prefs=notif_prefs.NOTIFICATION_PREFS,
                           notification_field_prefix=notif_prefs.FIELD_PREFIX,
                           can_edit_notifications=False,
                           title=_('Додати користувача'))


@admin_bp.route('/users/edit/<int:user_id>', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'manager')
def edit_user(user_id):
    user = User.query.get_or_404(user_id)

    allowed, msg = UserService.can_edit(current_user, user)
    if not allowed:
        flash(msg, 'danger')
        return redirect(url_for('admin.user_list', lang_code=g.lang_code))

    available_institutions = UserService.get_available_institutions(current_user)
    available_roles        = UserService.get_available_roles(current_user)

    form = UserEditForm(user_id=user.id)

    # Only the account owner (on their own profile page) and an admin may edit
    # the notification opt-outs — a manager can edit everything else here.
    can_edit_notifications = current_user.has_role('admin')

    # On POST preserve what was submitted; on GET seed from the saved user data.
    if request.method == 'POST':
        selected_inst_ids   = set(request.form.getlist('institutions'))
        selected_export_ids = set(request.form.getlist('can_export'))
        selected_role_ids   = set(request.form.getlist('roles'))
    else:
        selected_inst_ids   = {str(inst.id) for inst in user.institutions}
        selected_export_ids = {str(link.institution_id) for link in user.institution_links if link.can_export}
        selected_role_ids   = {str(role.id) for role in user.roles}

    if form.validate_on_submit():
        try:
            roles_before = {r.name for r in user.roles}
            UserService.update_user(
                user=user,
                available_roles=available_roles,
                username=form.username.data,
                email=form.email.data,
                phone=form.phone.data,
                first_name=form.first_name.data,
                last_name=form.last_name.data,
                new_password=form.password.data,
                selected_inst_ids=list(selected_inst_ids),
                can_export_ids=selected_export_ids,
                selected_role_ids=list(selected_role_ids),
            )
            # Notification opt-outs belong to the person; only they and an admin
            # may flip them, so a manager's POST never touches these columns
            # (the section is not rendered for them either).
            if can_edit_notifications:
                changed = notif_prefs.apply_form(user, request.form)
                if changed:
                    current_app.logger.info(
                        f"Notification prefs for user_id={user.id} changed by "
                        f"admin user_id={current_user.id}: {', '.join(changed)}")

            # Granting verification rights here must tell the person, exactly as
            # approving their request in the queue does — otherwise a
            # self-registered applicant approved from this form never learns it.
            new_roles = {r.name for r in user.roles} - roles_before
            granted_modules = [m for m, role in VerificationRequest.ROLE_BY_MODULE.items()
                               if role in new_roles]
            VerificationRequestService.resolve_pending_for_roles(
                user, current_user, new_roles)

            db.session.commit()

            if granted_modules and user.email:
                current_app.logger.info(
                    "Verification rights %s granted to user_id=%s by user_id=%s",
                    ','.join(sorted(granted_modules)), user.id, current_user.id)
                send_rights_granted_email(user, sorted(granted_modules))

            flash(f'Дані користувача {user.username} успішно оновлено!', 'success')
            return redirect(url_for('admin.user_list', lang_code=g.lang_code))
        except Exception as e:
            db.session.rollback()
            flash(f'Помилка при збереженні: {str(e)}', 'danger')

    if form.errors:
        for field_errors in form.errors.values():
            for err in field_errors:
                flash(err, 'danger')

    inst_groups = _build_inst_groups(available_institutions, g.lang_code)

    return render_template('admin_user_form.html',
                           form=form,
                           user=user,
                           inst_groups=inst_groups,
                           roles=available_roles,
                           selected_inst_ids=selected_inst_ids,
                           selected_export_ids=selected_export_ids,
                           selected_role_ids=selected_role_ids,
                           notification_prefs=notif_prefs.NOTIFICATION_PREFS,
                           notification_field_prefix=notif_prefs.FIELD_PREFIX,
                           can_edit_notifications=can_edit_notifications,
                           title=_('Редагувати користувача'))


@admin_bp.route('/users/delete/<int:user_id>', methods=['POST'])
@login_required
@role_required('admin', 'manager')
def delete_user(user_id):
    user = User.query.get_or_404(user_id)

    allowed, msg = UserService.can_delete(current_user, user)
    if not allowed:
        flash(msg, 'danger')
        return redirect(url_for('admin.user_list', lang_code=g.lang_code))

    try:
        username = user.username
        UserService.delete_user(user)
        db.session.commit()
        flash(f'Користувача {username} було успішно видалено.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Помилка при видаленні: {str(e)}', 'danger')

    return redirect(url_for('admin.user_list', lang_code=g.lang_code))


# ===========================================================================
# Institution management (admin only)
# ===========================================================================

@admin_bp.route('/institutions')
@login_required
@role_required('admin')
def institution_list():
    institutions = Institution.query.order_by(Institution.id).all()
    return render_template('admin_institutions_list.html', institutions=institutions)


@admin_bp.route('/institutions/add', methods=['GET', 'POST'])
@admin_bp.route('/institutions/edit/<int:inst_id>', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def edit_institution(inst_id=None):
    inst  = Institution.query.get_or_404(inst_id) if inst_id else None
    title = _('Редагувати установу') if inst else _('Додати установу')

    form = InstitutionForm()

    if form.validate_on_submit():
        code = form.code.data.strip()

        if not InstitutionService.is_code_unique(code, exclude_id=inst_id):
            flash(f'Установа з кодом "{code}" вже існує!', 'danger')
            return redirect(request.url)

        try:
            eco_uk, eco_en = InstitutionService.resolve_ecoregion(
                request.form.get('ecoregion_choice'),
                form.ecoregion_uk.data, form.ecoregion_en.data)
        except ValueError as e:
            flash(str(e), 'danger')
            return render_template('admin_institution_form.html', inst=inst, title=title,
                                   ecoregions=InstitutionService.get_ecoregions())

        try:
            if inst:
                InstitutionService.update(inst, form.name_uk.data, form.name_en.data, code,
                                          eco_uk, eco_en)
                flash(f'Дані установи "{form.name_uk.data}" оновлено.', 'success')
            else:
                InstitutionService.create(form.name_uk.data, form.name_en.data, code,
                                          eco_uk, eco_en)
                flash(f'Установу "{form.name_uk.data}" успішно створено!', 'success')
            db.session.commit()
            return redirect(url_for('admin.institution_list', lang_code=g.lang_code))
        except Exception as e:
            db.session.rollback()
            flash(f'Помилка збереження: {str(e)}', 'danger')

    if form.errors:
        for field_errors in form.errors.values():
            for err in field_errors:
                flash(err, 'danger')

    return render_template('admin_institution_form.html', inst=inst, title=title,
                           ecoregions=InstitutionService.get_ecoregions())


@admin_bp.route('/institutions/delete/<int:inst_id>', methods=['POST'])
@login_required
@role_required('admin')
def delete_institution(inst_id):
    inst = Institution.query.get_or_404(inst_id)
    try:
        name = inst.name_uk
        InstitutionService.delete(inst)
        db.session.commit()
        flash(f'Установу "{name}" було успішно видалено. Зв\'язки з користувачами анульовано.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Помилка при видаленні: {str(e)}', 'danger')

    return redirect(url_for('admin.institution_list', lang_code=g.lang_code))


# ===========================================================================
# Role management (admin only)
# ===========================================================================

@admin_bp.route('/roles')
@login_required
@role_required('admin')
def role_list():
    roles = Role.query.order_by(Role.id).all()
    return render_template('admin_roles_list.html', roles=roles)


@admin_bp.route('/roles/add', methods=['GET', 'POST'])
@admin_bp.route('/roles/edit/<int:role_id>', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def edit_role(role_id=None):
    role  = Role.query.get_or_404(role_id) if role_id else None
    title = _('Редагувати роль') if role else _('Додати роль')

    form = RoleForm()

    if form.validate_on_submit():
        name         = form.name.data.strip()
        assignable_by = form.assignable_by.data.strip() if form.assignable_by.data else None

        if not RoleService.is_name_unique(name, exclude_id=role_id):
            flash(f'Роль з назвою "{name}" вже існує!', 'danger')
            return redirect(request.url)

        if role and RoleService.is_system_role(role) and name != role.name:
            flash(f'Зміна системної назви для ролі "{role.name}" заборонена!', 'danger')
            return redirect(request.url)

        try:
            if role:
                RoleService.update(role, name, assignable_by)
                flash(f'Роль "{name}" успішно оновлено.', 'success')
            else:
                RoleService.create(name, assignable_by)
                flash(f'Роль "{name}" успішно створено!', 'success')
            db.session.commit()
            return redirect(url_for('admin.role_list', lang_code=g.lang_code))
        except Exception as e:
            db.session.rollback()
            flash(f'Помилка збереження: {str(e)}', 'danger')

    if form.errors:
        for field_errors in form.errors.values():
            for err in field_errors:
                flash(err, 'danger')

    return render_template('admin_role_form.html', role=role, title=title)


@admin_bp.route('/roles/delete/<int:role_id>', methods=['POST'])
@login_required
@role_required('admin')
def delete_role(role_id):
    role = Role.query.get_or_404(role_id)

    if RoleService.is_system_role(role):
        flash(f'Системну роль "{role.name}" не можна видаляти!', 'danger')
        return redirect(url_for('admin.role_list', lang_code=g.lang_code))

    try:
        name = role.name
        RoleService.delete(role)
        db.session.commit()
        flash(f'Роль "{name}" було успішно видалено.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Помилка при видаленні: {str(e)}', 'danger')

    return redirect(url_for('admin.role_list', lang_code=g.lang_code))


# ===========================================================================
# Contact form submissions (admin only)
# ===========================================================================

_VALID_STATUSES = {ContactSubmission.STATUS_NEW,
                   ContactSubmission.STATUS_READ,
                   ContactSubmission.STATUS_REPLIED}


@admin_bp.route('/contact-submissions')
@login_required
@role_required('admin')
def contact_submissions():
    """List contact-form submissions, newest first. Optional ?status= filter."""
    status = request.args.get('status')
    query = ContactSubmission.query
    if status in _VALID_STATUSES:
        query = query.filter_by(status=status)
    submissions = query.order_by(ContactSubmission.submitted_at.desc()).all()
    new_count = ContactSubmission.query.filter_by(
        status=ContactSubmission.STATUS_NEW).count()
    return render_template('admin_contact_submissions.html',
                           submissions=submissions,
                           active_status=status if status in _VALID_STATUSES else None,
                           new_count=new_count)


@admin_bp.route('/contact-submissions/<int:submission_id>/status', methods=['POST'])
@login_required
@role_required('admin')
def update_submission_status(submission_id):
    """Set a submission's status to one of new / read / replied."""
    submission = ContactSubmission.query.get_or_404(submission_id)
    new_status = request.form.get('status')
    if new_status not in _VALID_STATUSES:
        flash(_('Невідомий статус.'), 'danger')
        return redirect(url_for('admin.contact_submissions', lang_code=g.lang_code))
    try:
        submission.status = new_status
        db.session.commit()
        flash(_('Статус оновлено.'), 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Помилка: {str(e)}', 'danger')
    return redirect(url_for('admin.contact_submissions', lang_code=g.lang_code))


@admin_bp.route('/contact-submissions/<int:submission_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def delete_submission(submission_id):
    submission = ContactSubmission.query.get_or_404(submission_id)
    try:
        db.session.delete(submission)
        db.session.commit()
        flash(_('Звернення видалено.'), 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Помилка при видаленні: {str(e)}', 'danger')
    return redirect(url_for('admin.contact_submissions', lang_code=g.lang_code))


# ===========================================================================
# Verification requests (self-service registration)
# ===========================================================================

@admin_bp.route('/verification-requests')
@login_required
@role_required('admin', 'manager')
def verification_requests():
    """Queue of self-registered users asking for verification rights.

    A manager sees only requests naming an institution they have access to, and
    only those institutions inside each request — hence ``viewer``.
    """
    status = request.args.get('status', VerificationRequest.STATUS_PENDING)
    if status == 'all':
        status = None
    viewer = None if current_user.has_role('admin') else current_user
    requests_list = VerificationRequestService.list_requests(status=status,
                                                             viewer=viewer)
    scoped_rows = {req.id: VerificationRequestService.scope_rows(req, current_user)
                   for req in requests_list}
    return render_template('admin_verification_requests.html',
                           requests=requests_list,
                           scoped_rows=scoped_rows,
                           is_admin=current_user.has_role('admin'),
                           active_status=status,
                           pending_count=VerificationRequestService.pending_count(
                               viewer=viewer))


@admin_bp.route('/verification-requests/<int:request_id>/decide', methods=['POST'])
@login_required
@role_required('admin', 'manager')
def decide_verification_request(request_id):
    """Approve or reject one request and email the applicant the outcome.

    The decision covers only the institutions the decider owns: a manager's POST
    can neither grant nor remove another park's institution, and the request
    stays in the queue until every named institution has an answer.
    """
    req = VerificationRequest.query.get_or_404(request_id)

    action = request.form.get('action')
    if action not in ('approve', 'reject'):
        flash(_('Невідома дія.'), 'danger')
        return redirect(url_for('admin.verification_requests', lang_code=g.lang_code))

    # Absent field (a request naming no institution, or an older cached form) =>
    # "everything in my scope"; present-but-empty => the decider unticked them
    # all, which is a removal, not a full grant.
    if 'institutions' in request.form:
        institution_ids = request.form.getlist('institutions')
    else:
        institution_ids = None

    approve = (action == 'approve')
    ok, error, outcome = VerificationRequestService.decide(
        req, current_user, approve, institution_ids=institution_ids,
        note=request.form.get('note'))
    if not ok:
        flash(error, 'danger')
        return redirect(url_for('admin.verification_requests', lang_code=g.lang_code))

    granted_names = [i.label(g.lang_code) for i in outcome['granted']]
    removed_names = [i.label(g.lang_code) for i in outcome['removed']]

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        flash(f'Помилка при збереженні: {e}', 'danger')
        return redirect(url_for('admin.verification_requests', lang_code=g.lang_code))

    current_app.logger.info(
        "Verification request %s (%s) %s by user_id=%s granted=%s removed=%s closed=%s",
        req.id, req.module, 'approved' if approve else 'rejected', current_user.id,
        granted_names, removed_names, outcome['closed'])

    if req.user.email:
        # A partial approval is still good news for the applicant: it names what
        # was granted and says nothing about the institutions still deciding.
        notify_approved = approve and (bool(outcome['granted']) or outcome['role_granted'])
        if notify_approved or outcome['closed']:
            send_decision_email(req.user, req.module, notify_approved,
                                note=req.note, institutions=granted_names)

    if approve and granted_names:
        flash(_('Запит підтверджено для: %(names)s.', names=', '.join(granted_names)),
              'success')
    elif approve:
        flash(_('Запит підтверджено.'), 'success')
    else:
        flash(_('Запит відхилено.'), 'success')
    if removed_names and approve:
        flash(_('Вилучено зі запиту: %(names)s.', names=', '.join(removed_names)), 'info')
    if not outcome['closed']:
        flash(_('Запит залишається в черзі, бо очікує рішення інших установ.'), 'info')
    return redirect(url_for('admin.verification_requests', lang_code=g.lang_code))
