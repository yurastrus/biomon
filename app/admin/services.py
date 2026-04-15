# app/admin/services.py
"""
Бізнес-логіка адмін-модуля, відокремлена від HTTP-шару.

Кожен сервіс містить чисті функції/методи, які не знають про request/response.
Всі методи повертають дані або кидають виняток; flush/commit виконується в routes.py.
"""

from sqlalchemy import or_
from app.extensions import db, bcrypt
from app.models import User, Role, Institution, UserInstitution

# Ролі, що дають право на експорт
EXPORT_ROLES = frozenset({'analyst', 'manager', 'admin'})
# Системні ролі, які не можна перейменовувати або видаляти
SYSTEM_ROLES = frozenset({'admin', 'manager'})


# ===========================================================================
# UserService
# ===========================================================================

class UserService:

    @staticmethod
    def get_available_institutions(requester):
        """Повертає установи, доступні requester-у для призначення."""
        if requester.has_role('admin'):
            return Institution.query.all()
        return list(requester.institutions)

    @staticmethod
    def get_available_roles(requester):
        """Повертає ролі, які requester може призначати."""
        if requester.has_role('admin'):
            return Role.query.all()
        return Role.query.filter(
            or_(Role.assignable_by == None, Role.assignable_by == 'manager')
        ).all()

    @staticmethod
    def can_edit(requester, target):
        """
        Перевіряє, чи має requester право редагувати target.
        Повертає (True, None) або (False, повідомлення_про_помилку).
        """
        if requester.has_role('admin'):
            return True, None

        if target.has_role('admin'):
            return False, 'Доступ заборонено: Ви не можете редагувати адміністратора сайту.'

        my_ids = {i.id for i in requester.institutions}
        target_ids = {i.id for i in target.institutions}
        if not my_ids & target_ids:
            return False, 'Доступ заборонено: Цей користувач не належить до вашої установи.'

        return True, None

    @staticmethod
    def can_delete(requester, target):
        """
        Перевіряє, чи має requester право видаляти target.
        Повертає (True, None) або (False, повідомлення_про_помилку).
        """
        if requester.id == target.id:
            return False, 'Помилка: Ви не можете видалити власного користувача!'

        if requester.has_role('admin'):
            return True, None

        if target.created_by_id != requester.id:
            return False, 'Доступ заборонено: Ви можете видаляти лише створених вами користувачів.'

        my_ids = {i.id for i in requester.institutions}
        target_ids = {i.id for i in target.institutions}
        if not my_ids & target_ids:
            return False, 'Доступ заборонено: Цей користувач не належить до вашої установи.'

        return True, None

    @staticmethod
    def _role_names_for_ids(role_ids):
        """Повертає множину назв ролей за списком ID."""
        if not role_ids:
            return set()
        int_ids = [int(x) for x in role_ids]
        return {r.name for r in Role.query.filter(Role.id.in_(int_ids)).all()}

    @staticmethod
    def create_user(creator, username, password,
                    email, phone, first_name, last_name,
                    selected_inst_ids, can_export_ids, selected_role_ids):
        """
        Створює нового користувача та додає його до сесії (без commit).
        Повертає новий об'єкт User.
        """
        hashed_pw = bcrypt.generate_password_hash(password).decode('utf-8')
        new_user = User(
            username=username,
            password_hash=hashed_pw,
            email=email or None,
            phone=phone or None,
            first_name=first_name or None,
            last_name=last_name or None,
            created_by_id=creator.id,
        )

        role_names = UserService._role_names_for_ids(selected_role_ids)
        will_have_export = bool(role_names & EXPORT_ROLES)

        for i_id in selected_inst_ids:
            inst = Institution.query.get(int(i_id))
            if inst:
                new_user.institution_links.append(
                    UserInstitution(
                        institution_id=inst.id,
                        can_export=will_have_export and (str(i_id) in can_export_ids),
                    )
                )

        for r_id in selected_role_ids:
            role = Role.query.get(int(r_id))
            if role:
                new_user.roles.append(role)

        db.session.add(new_user)
        return new_user

    @staticmethod
    def update_user(user, available_roles,
                    username, email, phone, first_name, last_name,
                    new_password,
                    selected_inst_ids, can_export_ids, selected_role_ids):
        """
        Оновлює існуючого користувача (без commit).
        available_roles — список ролей, які бачить requester у формі
        (потрібен, щоб не зачепити приховані ролі, наприклад admin).
        """
        user.username = username
        user.email = email or None
        user.phone = phone or None
        user.first_name = first_name or None
        user.last_name = last_name or None

        if new_password:
            user.password_hash = bcrypt.generate_password_hash(new_password).decode('utf-8')

        # Визначаємо чи буде право на експорт після оновлення
        selected_role_names = UserService._role_names_for_ids(selected_role_ids)
        # Порівнюємо за ID (не по ідентичності об'єкта) — безпечно між сесіями
        available_role_ids = {r.id for r in available_roles}
        hidden_role_names = {r.name for r in user.roles if r.id not in available_role_ids}
        will_have_export = bool((selected_role_names | hidden_role_names) & EXPORT_ROLES)

        # Зберігаємо поточні can_export-значення (щоб не втратити при зміні ролі)
        existing_export_map = {link.institution_id: link.can_export for link in user.institution_links}

        user.institution_links = []
        for i_id in selected_inst_ids:
            inst = Institution.query.get(int(i_id))
            if inst:
                if will_have_export:
                    new_can_export = (str(i_id) in can_export_ids)
                else:
                    new_can_export = existing_export_map.get(inst.id, False)
                user.institution_links.append(
                    UserInstitution(institution_id=inst.id, can_export=new_can_export)
                )

        # Менеджер не повинен випадково видалити ролі, які він не бачить у формі
        # Порівнюємо за ID — гарантовано коректно незалежно від identity map
        roles_to_keep = [r for r in user.roles if r.id not in available_role_ids]
        user.roles = roles_to_keep
        for r_id in selected_role_ids:
            role = Role.query.get(int(r_id))
            if role:
                user.roles.append(role)

    @staticmethod
    def delete_user(user):
        """Видаляє користувача із сесії (без commit)."""
        db.session.delete(user)


# ===========================================================================
# InstitutionService
# ===========================================================================

class InstitutionService:

    @staticmethod
    def is_code_unique(code, exclude_id=None):
        """True якщо код унікальний (exclude_id — для режиму редагування)."""
        existing = Institution.query.filter_by(code=code).first()
        if existing is None:
            return True
        if exclude_id and existing.id == exclude_id:
            return True
        return False

    @staticmethod
    def create(name_uk, name_en, code):
        """Створює установу та додає до сесії (без commit)."""
        inst = Institution(name_uk=name_uk, name_en=name_en or None, code=code)
        db.session.add(inst)
        return inst

    @staticmethod
    def update(inst, name_uk, name_en, code):
        """Оновлює установу (без commit)."""
        inst.name_uk = name_uk
        inst.name_en = name_en or None
        inst.code = code

    @staticmethod
    def delete(inst):
        """Видаляє установу із сесії (без commit)."""
        db.session.delete(inst)


# ===========================================================================
# RoleService
# ===========================================================================

class RoleService:

    @staticmethod
    def is_name_unique(name, exclude_id=None):
        """True якщо назва ролі унікальна."""
        existing = Role.query.filter_by(name=name).first()
        if existing is None:
            return True
        if exclude_id and existing.id == exclude_id:
            return True
        return False

    @staticmethod
    def is_system_role(role):
        """True якщо роль системна (admin/manager) — її не можна видаляти/перейменовувати."""
        return role.name in SYSTEM_ROLES

    @staticmethod
    def create(name, assignable_by):
        """Створює роль та додає до сесії (без commit)."""
        role = Role(name=name, assignable_by=assignable_by or None)
        db.session.add(role)
        return role

    @staticmethod
    def update(role, name, assignable_by):
        """Оновлює роль (без commit)."""
        role.name = name
        role.assignable_by = assignable_by or None

    @staticmethod
    def delete(role):
        """Видаляє роль із сесії (без commit)."""
        db.session.delete(role)
