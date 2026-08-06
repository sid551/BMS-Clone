# Admin Dashboard Access & Administrator Credentials

This document provides administrative access details, superuser setup instructions, and role-based permission guidelines for the Custom Admin Dashboard in the BookMySeat Movie Booking System.

---

## 1. Existing Administrator Credentials

For local evaluation, development, and administrative testing:

| Parameter | Credential Value |
| :--- | :--- |
| **Admin Portal URL** | `/movies/manage/` |
| **Django Native Admin URL** | `/admin/` |
| **Username** | `sidd` |
| **Email** | `sidd@gmail.com` |
| **Access Rights** | `is_staff = True`, `is_superuser = True` |

---

## 2. Access Control & Permission Rules

The Custom Admin Dashboard enforces strict role-based access control via `@staff_or_admin_required`:

1. **Authenticated Administrator / Staff (`is_staff=True` or `is_superuser=True`)**:
   - Granted full access to `/movies/manage/` analytics dashboard, CSV streaming exports, catalog management, and moderation tools.
2. **Authenticated Non-Staff User (`is_staff=False`)**:
   - Access denied with an explicit **`403 Forbidden`** response header and permission notice.
3. **Unauthenticated Guest User**:
   - Automatically redirected to `/login/?next=/movies/manage/`.

---

## 3. Creating Additional Administrator Accounts

To create a new superuser account via the Django Command Line Interface (CLI):

### Step 1: Open Terminal in Project Root
```bash
cd "c:\Internship BMS\djnago-bookmyshow-clone"
```

### Step 2: Run `createsuperuser` Command
```bash
python manage.py createsuperuser
```

### Step 3: Enter Required Fields
```text
Username: admin2
Email address: admin2@example.com
Password: <YourSecurePassword>
Password (again): <YourSecurePassword>
Superuser created successfully.
```

---

## 4. Granting Staff Permissions to Existing Users

To promote an existing user account to Staff status via Django Shell:

```bash
python manage.py shell
```

```python
from django.contrib.auth import get_user_model
User = get_user_model()

user = User.objects.get(username='john_doe')
user.is_staff = True
user.save()
print(f"Granted staff permissions to {user.username}")
```
