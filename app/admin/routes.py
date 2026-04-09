# app/admin/routes.py

from flask import render_template, g, current_app, request, redirect, url_for, flash
from flask_login import login_required, current_user
from flask_babel import gettext as _
from sqlalchemy import or_
from app.models import User, Institution, Role, UserInstitution
from app.utils.decorators import role_required
from . import admin_bp

@admin_bp.route('/')
@login_required
@role_required('admin', 'manager')
def home():
    # g.lang_code вже встановлено автоматично в __init__.py модуля
    return render_template('admin_home.html')

@admin_bp.route('/users')
@login_required
@role_required('admin', 'manager')
def user_list():
    if current_user.has_role('admin'):
        users = User.query.order_by(User.id.desc()).all()
    else:
        # Менеджер бачить ТІЛЬКИ тих, кого він сам створив
        users = User.query.filter_by(created_by_id=current_user.id).order_by(User.id.desc()).all()

    return render_template('admin_users_list.html', users=users)

@admin_bp.route('/users/add', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'manager')
def add_user():
    from app.extensions import db, bcrypt
    from app.models import Role, Institution
    
    # 1. Фільтруємо доступні установи та ролі залежно від того, ХТО створює
    if current_user.has_role('admin'):
        # Глобальний адмін бачить усе
        available_institutions = Institution.query.all()
        available_roles = Role.query.all()
    else:
        # Локальний адмін бачить ТІЛЬКИ свої установи
        available_institutions = current_user.institutions
        # Локальний адмін може призначати лише "безпечні" ролі (не може створити іншого адміна чи менеджера)
        available_roles = Role.query.filter(
            or_(Role.assignable_by == None, Role.assignable_by == 'manager')
        ).all()

    if request.method == 'POST':
        # Отримуємо дані з форми
        username = request.form.get('username')
        password = request.form.get('password')
        email = request.form.get('email')
        phone = request.form.get('phone')
        first_name = request.form.get('first_name')
        last_name = request.form.get('last_name')
        
        # ID вибраних установ та ролей (списки)
        selected_inst_ids = request.form.getlist('institutions')
        can_export_ids    = set(request.form.getlist('can_export'))
        selected_role_ids = request.form.getlist('roles')

        # --- ПЕРЕВІРКА ДЛЯ ЛОКАЛЬНОГО АДМІНА ---
        if not current_user.has_role('admin'):
            # Якщо творцем цього користувача є не поточний менеджер — блокуємо доступ
            if user.created_by_id != current_user.id:
                flash('Доступ заборонено: Ви можете редагувати лише створених вами користувачів.', 'danger')
                return redirect(url_for('admin.user_list', lang_code=g.lang_code))

        # 2. Створюємо користувача
        hashed_pw = bcrypt.generate_password_hash(password).decode('utf-8')
        new_user = User(
            username=username,
            password_hash=hashed_pw,
            email=email,
            phone=phone,
            first_name=first_name,
            last_name=last_name,
            created_by_id=current_user.id
        )

        # Додаємо зв'язки з установами
        for i_id in selected_inst_ids:
            inst = Institution.query.get(int(i_id))
            if inst:
                new_user.institution_links.append(
                    UserInstitution(institution_id=inst.id, can_export=(i_id in can_export_ids))
                )

        # Додаємо зв'язки з ролями
        for r_id in selected_role_ids:
            role = Role.query.get(int(r_id))
            if role:
                new_user.roles.append(role)

        try:
            db.session.add(new_user)
            db.session.commit()
            flash(f'Користувача {username} успішно створено!', 'success')
            return redirect(url_for('admin.user_list', lang_code=g.lang_code))
        except Exception as e:
            db.session.rollback()
            flash(f'Помилка при збереженні: {str(e)}', 'danger')

    return render_template('admin_user_form.html',
                           institutions=available_institutions,
                           roles=available_roles,
                           export_institution_ids=set(),
                           title=_('Додати користувача'))


@admin_bp.route('/users/edit/<int:user_id>', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'manager')
def edit_user(user_id):
    from app.extensions import db, bcrypt
    from app.models import Role, Institution
    
    # 1. Знаходимо користувача, якого хочемо редагувати
    user = User.query.get_or_404(user_id)

    # --- БЕЗПЕКА ---
    # Якщо поточний користувач - менеджер, перевіряємо обмеження:
    if not current_user.has_role('admin'):
        # А) Менеджер не може редагувати адмінів
        if user.has_role('admin'):
            flash('Доступ заборонено: Ви не можете редагувати адміністратора сайту.', 'danger')
            return redirect(url_for('admin.user_list', lang_code=g.lang_code))
        
        # Б) Менеджер може редагувати лише користувачів СВОЇХ установ
        my_inst_ids = [i.id for i in current_user.institutions]
        target_user_inst_ids = [i.id for i in user.institutions]
        # Якщо немає жодної спільної установи
        if not any(inst_id in my_inst_ids for inst_id in target_user_inst_ids):
            flash('Доступ заборонено: Цей користувач не належить до вашої установи.', 'danger')
            return redirect(url_for('admin.user_list', lang_code=g.lang_code))

    # 2. Фільтруємо доступні установи та ролі для форми (як у add_user)
    if current_user.has_role('admin'):
        available_institutions = Institution.query.all()
        available_roles = Role.query.all()
    else:
        available_institutions = current_user.institutions
        available_roles = Role.query.filter(
            or_(Role.assignable_by == None, Role.assignable_by == 'manager')
        ).all()

    if request.method == 'POST':
        user.username = request.form.get('username')
        user.email = request.form.get('email') or None
        user.phone = request.form.get('phone') or None
        user.first_name = request.form.get('first_name') or None
        user.last_name = request.form.get('last_name') or None

        
        # Оновлення пароля тільки якщо поле не порожнє
        new_password = request.form.get('password')
        if new_password:
            user.password_hash = bcrypt.generate_password_hash(new_password).decode('utf-8')

        # Оновлення установ
        selected_inst_ids = request.form.getlist('institutions')
        can_export_ids    = set(request.form.getlist('can_export'))
        user.institution_links = []  # Очищуємо старі зв'язки
        for i_id in selected_inst_ids:
            inst = Institution.query.get(int(i_id))
            if inst:
                user.institution_links.append(
                    UserInstitution(institution_id=inst.id, can_export=(i_id in can_export_ids))
                )

        # Оновлення ролей
        selected_role_ids = request.form.getlist('roles')
        # Важливо: менеджер не повинен випадково видалити роль 'admin', якщо він її не бачить у формі
        # Тому ми видаляємо тільки ті ролі, які були доступні менеджеру для вибору
        roles_to_keep = []
        if not current_user.has_role('admin'):
            # Залишаємо ті ролі, які менеджер НЕ міг бачити (наприклад, роль admin у іншого менеджера)
            roles_to_keep = [r for r in user.roles if r not in available_roles]
        
        user.roles = roles_to_keep
        for r_id in selected_role_ids:
            role = Role.query.get(int(r_id))
            if role: user.roles.append(role)

        db.session.commit()
        flash(f'Дані користувача {user.username} успішно оновлено!', 'success')
        return redirect(url_for('admin.user_list', lang_code=g.lang_code))

    export_institution_ids = {link.institution_id for link in user.institution_links if link.can_export}
    return render_template('admin_user_form.html',
                           user=user,
                           institutions=available_institutions,
                           roles=available_roles,
                           export_institution_ids=export_institution_ids,
                           title=_('Редагувати користувача'))

@admin_bp.route('/users/delete/<int:user_id>', methods=['POST'])
@login_required
@role_required('admin', 'manager')
def delete_user(user_id):
    from app.extensions import db
    
    # 1. Знаходимо користувача
    user = User.query.get_or_404(user_id)

    # 2. Перевірки безпеки
    if user.id == current_user.id:
        flash('Помилка: Ви не можете видалити власного користувача!', 'danger')
        return redirect(url_for('admin.user_list', lang_code=g.lang_code))

    if not current_user.has_role('admin'):
        # Менеджер може видаляти лише своїх
        if user.created_by_id != current_user.id:
            flash('Доступ заборонено: Ви можете видаляти лише створених вами користувачів.', 'danger')
            return redirect(url_for('admin.user_list', lang_code=g.lang_code))
        
        # Менеджер може видаляти лише користувачів своїх установ
        my_inst_ids = [i.id for i in current_user.institutions]
        target_inst_ids = [i.id for i in user.institutions]
        if not any(inst_id in my_inst_ids for inst_id in target_inst_ids):
            flash('Доступ заборонено: Цей користувач не належить до вашої установи.', 'danger')
            return redirect(url_for('admin.user_list', lang_code=g.lang_code))

    # 3. Видалення
    try:
        username = user.username
        db.session.delete(user)
        db.session.commit()
        flash(f'Користувача {username} було успішно видалено.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Помилка при видаленні: {str(e)}', 'danger')

    return redirect(url_for('admin.user_list', lang_code=g.lang_code))


# ==========================================
# УПРАВЛІННЯ УСТАНОВАМИ (Тільки для Admin)
# ==========================================

@admin_bp.route('/institutions')
@login_required
@role_required('admin')  # Тільки глобальний адмін!
def institution_list():
    """Список усіх установ."""
    institutions = Institution.query.order_by(Institution.id).all()
    return render_template('admin_institutions_list.html', institutions=institutions)

@admin_bp.route('/institutions/delete/<int:inst_id>', methods=['POST'])
@login_required
@role_required('admin')
def delete_institution(inst_id):
    """Видалення установи."""
    from app.extensions import db
    inst = Institution.query.get_or_404(inst_id)
    
    try:
        name = inst.name_uk
        db.session.delete(inst)
        db.session.commit()
        flash(f'Установу "{name}" було успішно видалено. Зв\'язки з користувачами анульовано.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Помилка при видаленні: {str(e)}', 'danger')

    return redirect(url_for('admin.institution_list', lang_code=g.lang_code))


@admin_bp.route('/institutions/add', methods=['GET', 'POST'])
@admin_bp.route('/institutions/edit/<int:inst_id>', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def edit_institution(inst_id=None):
    from app.extensions import db
    
    # Якщо є ID - ми редагуємо, інакше - створюємо нову
    if inst_id:
        inst = Institution.query.get_or_404(inst_id)
        title = _('Редагувати установу')
    else:
        inst = None
        title = _('Додати установу')

    if request.method == 'POST':
        name_uk = request.form.get('name_uk')
        name_en = request.form.get('name_en')
        code = request.form.get('code')

        # Валідація: код має бути унікальним
        existing = Institution.query.filter_by(code=code).first()
        if existing and (not inst or existing.id != inst.id):
            flash(f'Установа з кодом "{code}" вже існує!', 'danger')
            return redirect(request.url)

        if inst:
            # Оновлюємо існуючу
            inst.name_uk = name_uk
            inst.name_en = name_en
            inst.code = code
            flash(f'Дані установи "{name_uk}" оновлено.', 'success')
        else:
            # Створюємо нову
            new_inst = Institution(name_uk=name_uk, name_en=name_en, code=code)
            db.session.add(new_inst)
            flash(f'Установу "{name_uk}" успішно створено!', 'success')

        try:
            db.session.commit()
            return redirect(url_for('admin.institution_list', lang_code=g.lang_code))
        except Exception as e:
            db.session.rollback()
            flash(f'Помилка збереження: {str(e)}', 'danger')

    return render_template('admin_institution_form.html', inst=inst, title=title)


# ==========================================
# УПРАВЛІННЯ РОЛЯМИ (Тільки для Admin)
# ==========================================

@admin_bp.route('/roles')
@login_required
@role_required('admin')
def role_list():
    """Список усіх ролей."""
    from app.models import Role
    roles = Role.query.order_by(Role.id).all()
    return render_template('admin_roles_list.html', roles=roles)

@admin_bp.route('/roles/add', methods=['GET', 'POST'])
@admin_bp.route('/roles/edit/<int:role_id>', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def edit_role(role_id=None):
    """Створення та редагування ролі."""
    from app.extensions import db
    from app.models import Role
    
    if role_id:
        role = Role.query.get_or_404(role_id)
        title = _('Редагувати роль')
    else:
        role = None
        title = _('Додати роль')

    if request.method == 'POST':
        name = request.form.get('name').strip()
        assignable_by = request.form.get('assignable_by')
        
        # Якщо передано порожній рядок, перетворюємо його на NULL (Python None)
        if not assignable_by:
            assignable_by = None

        # Валідація: назва має бути унікальною
        existing = Role.query.filter_by(name=name).first()
        if existing and (not role or existing.id != role.id):
            flash(f'Роль з назвою "{name}" вже існує!', 'danger')
            return redirect(request.url)

        # Захист від зміни імені критичних ролей
        if role and role.name in ['admin', 'manager'] and name != role.name:
            flash(f'Зміна системної назви для ролі "{role.name}" заборонена!', 'danger')
            return redirect(request.url)

        if role:
            role.name = name
            role.assignable_by = assignable_by
            flash(f'Роль "{name}" успішно оновлено.', 'success')
        else:
            new_role = Role(name=name, assignable_by=assignable_by)
            db.session.add(new_role)
            flash(f'Роль "{name}" успішно створено!', 'success')

        try:
            db.session.commit()
            return redirect(url_for('admin.role_list', lang_code=g.lang_code))
        except Exception as e:
            db.session.rollback()
            flash(f'Помилка збереження: {str(e)}', 'danger')

    return render_template('admin_role_form.html', role=role, title=title)

@admin_bp.route('/roles/delete/<int:role_id>', methods=['POST'])
@login_required
@role_required('admin')
def delete_role(role_id):
    """Видалення ролі."""
    from app.extensions import db
    from app.models import Role
    
    role = Role.query.get_or_404(role_id)
    
    # Захист системних ролей від видалення
    if role.name in ['admin', 'manager']:
        flash(f'Системну роль "{role.name}" не можна видаляти!', 'danger')
        return redirect(url_for('admin.role_list', lang_code=g.lang_code))
        
    try:
        name = role.name
        db.session.delete(role)
        db.session.commit()
        flash(f'Роль "{name}" було успішно видалено.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Помилка при видаленні: {str(e)}', 'danger')

    return redirect(url_for('admin.role_list', lang_code=g.lang_code))