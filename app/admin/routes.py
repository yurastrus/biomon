from flask import render_template, g, request, redirect, url_for, flash
from flask_login import login_required, current_user
from flask_babel import gettext as _

from app.extensions import db
from app.models import User, Institution, Role
from app.utils.decorators import role_required
from app.admin.forms import UserCreateForm, UserEditForm, InstitutionForm, RoleForm
from app.admin.services import UserService, InstitutionService, RoleService
from . import admin_bp


# ===========================================================================
# Admin panel home
# ===========================================================================

@admin_bp.route('/')
@login_required
@role_required('admin', 'manager')
def home():
    return render_template('admin_home.html')


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

    if form.validate_on_submit():
        selected_inst_ids = request.form.getlist('institutions')
        can_export_ids    = set(request.form.getlist('can_export'))
        selected_role_ids = request.form.getlist('roles')

        try:
            UserService.create_user(
                creator=current_user,
                username=form.username.data,
                password=form.password.data,
                email=form.email.data,
                phone=form.phone.data,
                first_name=form.first_name.data,
                last_name=form.last_name.data,
                selected_inst_ids=selected_inst_ids,
                can_export_ids=can_export_ids,
                selected_role_ids=selected_role_ids,
            )
            db.session.commit()
            flash(f'Користувача {form.username.data} успішно створено!', 'success')
            return redirect(url_for('admin.user_list', lang_code=g.lang_code))
        except Exception as e:
            db.session.rollback()
            flash(f'Помилка при збереженні: {str(e)}', 'danger')

    # Surface validation errors via flash (template is not modified)
    if form.errors:
        for field_errors in form.errors.values():
            for err in field_errors:
                flash(err, 'danger')

    return render_template('admin_user_form.html',
                           form=form,
                           institutions=available_institutions,
                           roles=available_roles,
                           export_institution_ids=set(),
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

    if form.validate_on_submit():
        selected_inst_ids = request.form.getlist('institutions')
        can_export_ids    = set(request.form.getlist('can_export'))
        selected_role_ids = request.form.getlist('roles')

        try:
            UserService.update_user(
                user=user,
                available_roles=available_roles,
                username=form.username.data,
                email=form.email.data,
                phone=form.phone.data,
                first_name=form.first_name.data,
                last_name=form.last_name.data,
                new_password=form.password.data,
                selected_inst_ids=selected_inst_ids,
                can_export_ids=can_export_ids,
                selected_role_ids=selected_role_ids,
            )
            db.session.commit()
            flash(f'Дані користувача {user.username} успішно оновлено!', 'success')
            return redirect(url_for('admin.user_list', lang_code=g.lang_code))
        except Exception as e:
            db.session.rollback()
            flash(f'Помилка при збереженні: {str(e)}', 'danger')

    if form.errors:
        for field_errors in form.errors.values():
            for err in field_errors:
                flash(err, 'danger')

    export_institution_ids = {link.institution_id for link in user.institution_links if link.can_export}
    return render_template('admin_user_form.html',
                           form=form,
                           user=user,
                           institutions=available_institutions,
                           roles=available_roles,
                           export_institution_ids=export_institution_ids,
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
            if inst:
                InstitutionService.update(inst, form.name_uk.data, form.name_en.data, code)
                flash(f'Дані установи "{form.name_uk.data}" оновлено.', 'success')
            else:
                InstitutionService.create(form.name_uk.data, form.name_en.data, code)
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

    return render_template('admin_institution_form.html', inst=inst, title=title)


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
