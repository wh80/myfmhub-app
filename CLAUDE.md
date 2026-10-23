# flask-play

A facilities management (CAFM-style) SaaS app, pre-production. It tracks sites, assets, jobs (reactive work), job schedules (planned/recurring maintenance) and suppliers.

**Current priority: build out features first. Design and UI polish come later.** Don't restyle or refactor templates unless asked. See "Deferred: design" below.

## Stack

- Flask 3, Flask-SQLAlchemy, Flask-Migrate (Alembic), Flask-WTF (CSRF is on globally), `email-validator` (for WTForms `Email()`), `resend` (email), `reportlab` (PDF downloads), `tzdata` (UK time zone on Windows), `psycopg[binary]` (SQLAlchemy 2.1 uses psycopg 3 for `postgresql://` URLs) and `gunicorn` (production). They're pinned in `requirements.txt` (direct dependencies only); add new packages there.
- PostgreSQL. Primary keys are UUIDs (`sqlalchemy.dialects.postgresql.UUID(as_uuid=True)`)
- Server-rendered Jinja templates with Bootstrap 5.3 and Bootstrap Icons 1.11.3 (`<i class="bi bi-...">`) from the CDN with SRI hashes, plus a small `app/static/js/app.js` (toast auto-hide, the person form's group toggle, table Columns menus)
- Windows dev machine, venv at `venv/`, run with `venv/Scripts/python.exe`
- Not a git repo yet. When one is created, add `.env` to `.gitignore` before the first commit.

## Running

```
venv/Scripts/python.exe run.py               # dev server (debug)
flask db migrate / flask db upgrade          # migrations
venv/Scripts/python.exe seeder.py            # add 50 of each record type
venv/Scripts/python.exe seeder.py --clear    # wipe data first; --count N to change amount
flask make-platform-admin <email>            # grant /admin access (--remove to take it away)
flask create-jobs-for-job-schedules          # raise jobs from due job schedules (run daily by cron)
```

Config comes from `.env` via `config.py` (all of these are loaded into `Config`): `DATABASE_URL`, `SECRET_KEY`, `OTP_PEPPER`, `DO_SPACES_*`, `RESEND_API_KEY`, `EMAIL_FROM`, `SUPPORT_EMAIL` (default support@myfmhub.com). Never hard-code secrets in `config.py`.

In local dev, `RESEND_API_KEY` is commented out in `.env`, so sign-in emails, codes included, are printed to the dev server console. That's how you sign in as seeded `@example.com` users. Uncomment it to send real email.

## Data model (`app/models.py`)

- Multi-tenant. `Organisation` (name, plus `suspended_at`, which a platform admin sets so nobody in it can sign in) is the tenant. Every other model except `OTP` and `UserSession` (both scoped through their user) has a non-null `organisation_id`, and `Organisation` has a `cascade='delete'` relationship to each. Link tables carry no organisation.
- `User` is the authentication model:
  - unique `email`
  - `email_verified_at`, set by the first successful OTP
  - `user_group_id` (required): the user's permission group, see `UserGroup` below. `is_org_admin` is True when that group is the organisation's Admin group.
  - `is_platform_admin`: MyFmHub staff access to `/admin`. It's only set with the CLI command.
  - `can_login` (default False): the organisation's choice, set on the person's record. Registration sets it to True. When it's False, login, pending codes and existing sessions are all refused (`NO_LOGIN_MESSAGE`).
  - The unique constraint is case-sensitive, so lowercase emails before saving or looking them up. There's no password: login is passwordless (OTP).
- `Person` is for operational and display use: `first_name`, `last_name`, `email`, plus optional `job_title` and `mobile`. It has an optional one-to-one link to `User` through `Person.user_id` (unique, ondelete SET NULL), accessed as `user.person` and `person.user`. The model allows a person without a user, but the app always creates them in pairs: every Person has a User, and "no login" means `user.can_login = False`. The seeder follows this too. `Person.email` and `User.email` must always match. Assigning a user to a second person quietly moves the link (the old person's `user_id` becomes NULL).
- `UserGroup` belongs to an `Organisation` (`organisation.user_groups`, cascade delete) and has `users`. It stores `name` (unique per organisation) and `is_admin`.
  - `UserGroupPermission` rows (`user_group_id` and `permission`, composite PK, no id or timestamps, like a link table) are the permissions it's granted. `user_group.permission_keys` returns them as a set.
  - Registration calls `create_default_groups(org)`, which makes Admin (`is_admin`, full access, can't be renamed, changed or deleted) and Team (every grid permission). The registrant goes in Admin. The seeder does the same: first user Admin, the rest Team.
- `OTP` belongs to a `User` (ondelete CASCADE, `user.otps`). It stores `code_hash`, an HMAC-SHA256 of the 6-digit code peppered with `OTP_PEPPER`, never the code itself. It also has `expires_at` and `used_at` (both `DateTime(timezone=True)`, unlike the naive `created_at`/`updated_at`; SQLite returns them naive) and `attempts`.
- `UserSession` belongs to a `User` (ondelete CASCADE, `user.sessions`). It stores:
  - `token_hash`: SHA-256 of the cookie token, unique
  - `expires_at`: 30 days
  - `revoked_at`: set it to log a session out
  - `last_seen_at`: written at most every 5 minutes
  - `user_agent` and `ip_address`: `request.remote_addr`. Behind a proxy or load balancer, this needs Werkzeug's `ProxyFix`. It's named `UserSession` to avoid a clash with Flask's `session` and `db.session`.
- `Site` has many `Job`, `Asset` and `JobSchedule`, with cascade delete.
- `Job` belongs to a `Site`, and optionally to a `JobSchedule` (`job_schedule_id`, ondelete RESTRICT).
  - `status` is a key from `JOB_STATUSES` in `app/models.py` (`new`, `open`, `closed`; default `new`, enforced by the `ck_job_status` check constraint). `job.status_label` gives the display text and `jobs/_status_badge.html` renders it as a badge. It's set by the app only, never on `JobForm`.
  - `source` is a key from `JOB_SOURCES` (`planned`, `reactive`, `remedial`; `ck_job_source` check constraint), shown with `job.source_label`. It's required with no default at any level, so every code path that creates a job must set it: the create route sets `reactive`, and raising a job from a schedule should set `planned`. Not on `JobForm`.
  - `closed_at` (Date, nullable) is set only by closing: the job header's Close button (shown with `jobs.edit` while the job isn't closed) links to GET `/jobs/<id>/close` (`jobs.close`, needs `jobs.edit`). The page has a `JobCloseForm` defaulting to the UK date (`uk_today`), which can't be in the future. POST sets status `closed` and `closed_at`. An already-closed job redirects back with a warning. A closed job shows a Reopen button instead: a POST form to `/jobs/<id>/reopen` (`jobs.reopen`, needs `jobs.edit`, POST only) that sets status `open` and clears `closed_at`.
- `JobSchedule` belongs to a `Site` and has many `jobs`. `next_due_by`, `frequency` (a key from `FREQUENCIES` in `app/services/scheduling.py`; `job_schedule.frequency_label` gives the display text), `notice_days` (0–365, and no more than the frequency's shortest gap in `FREQUENCY_MIN_DAYS`, checked by `JobScheduleForm.validate_notice_days`) and `next_job_creation_date` are required. `next_job_creation_date` is never on a form: create and edit set it with `job_creation_date(next_due_by, notice_days)`. It can be in the past when the due date is close.
- `Asset` belongs to a `Site`. `asset_number` and `serial_number` are optional (blank stored as NULL) and each unique per organisation: there's a DB `UniqueConstraint` with `organisation_id`, and `check_unique()` in `routes/assets.py` checks case-insensitively and adds form errors. It links many-to-many to jobs (`job_assets` table) and to job schedules (`job_schedule_assets` table). The link FKs use ondelete CASCADE, so deleting a record removes only its link rows.
- `ImportEvent` records one CSV import. It stores `import_type` (a key from `IMPORT_TYPES`), `filename`, `rows_imported`, `errors` (JSON `[{'row', 'error'}]`, with the header as row 1), the organisation, `user_id`, and `undone_at`/`undone_by_id` (both user FKs ondelete SET NULL; the relationships need `foreign_keys`). `Site`, `Asset`, `JobSchedule`, `Supplier` and `Person` have a nullable `import_event_id` (ondelete SET NULL) set on imported records.
- Every model has `id`, `created_at` and `updated_at`.
- History: these were renamed from WorkOrder → Job, PPMSchedule → JobSchedule, Contractor → Supplier. Old names should not appear anywhere.
- Migrations: a single regenerated `init` migration. It's pre-production, so the user deletes and regenerates migrations rather than stacking them.

## Code layout and conventions

- Routes: one blueprint per resource in `app/routes/`: `sites`, `assets`, `jobs`, `job_schedules` (URL `/job-schedules`), `suppliers`, `people`, `user_groups` (URL `/user-groups`). All are registered in `app/__init__.py`. URLs use hyphens; endpoints and templates use underscores.
- `organisations` blueprint (`/organisations` prefix) holds `register()`, but that view is mounted at the root `/register` (`/login` is mounted the same way, see `auth` below) with `app.add_url_rule(..., endpoint='organisations.register')` in `create_app`, so `url_for('organisations.register')` gives `/register`. Registration creates an Organisation, a User (email stripped and lowercased, duplicate check rejects the form) and a linked Person in one commit. On success it calls `create_and_send_otp` and redirects to the validate page. If sending fails, the account is kept and it flashes a warning and redirects to `/login`, where they can ask for a code. No password: login will be passwordless.
- **People** (`/people`, `app/routes/people.py`):
  - Index, view, create, edit and delete like the other resources, gated by the `people.*` permissions like everything else.
  - The form has a required User Group select, built from `org_query(UserGroup)`. Non-admins don't see the Admin group in the list, can't edit, delete or resend to an admin (`require_can_manage`, 403), and can't change their own group.
  - The organisation must always have at least one admin who can log in (`other_active_admins(user)`). Edit refuses to move the last one out of Admin or turn off their login, and delete refuses to remove them.
  - After an edit, if you can no longer see people (you moved yourself to a smaller group), you're sent to the dashboard.
  - Templates: flat `people/view.html` (no tabs) and an inline table in `index.html`. The search covers first, last and full name, email and job title.
  - Create makes the Person and its User in one commit. The email is lowercased and `email_taken()` checks it against every user in every organisation.
  - Edit updates `person.email` and `user.email` together; a change resets `email_verified_at`.
  - Turning `can_login` on (on create or edit) calls `send_invite()`, which checks the rate limit, then `create_and_send_otp(..., invited_by=<name>)`, and flashes the outcome. Turning it off calls `revoke_user_sessions`.
  - POST `/people/<id>/send-invite` (needs `people.edit`) is the "Resend sign-in email" button on the view page. It shows when `can_login` is on and it isn't your own record, and it calls `send_invite()`.
  - Delete removes the user, and with it their sessions and codes, and the person in one commit.
  - Nobody can remove their own `can_login` or delete themselves.
- **Permissions** (`app/services/permissions.py`):
  - Keys are `<resource>.<action>`. `RESOURCES` (sites, assets, jobs, job_schedules, suppliers, people) × `ACTIONS` (view, create, edit, delete) make up `GRANTABLE`, the grid on the user group pages.
  - `ADMIN_ONLY` (`organisation.manage`, `sessions.manage`, `user_groups.manage`) is never stored on a group: only the Admin group has these.
  - `can(user, key)`: the Admin group always passes, otherwise the key must be in the group's `permission_keys`. Templates use the global `can('jobs.edit')` (current user) to hide buttons and navbar links.
  - `clean_permissions()` drops unknown and admin-only keys, and adds `<resource>.view` whenever create, edit or delete is given. `set_permissions(group, keys)` applies the difference.
  - **Enforcement**: every resource blueprint and `user_groups` registers `before_request(require_login)` then `before_request(require_permission)`. `require_permission` looks up `request.endpoint` in `ENDPOINT_PERMISSIONS` and gives a 403 if the group lacks the permission, or if the endpoint isn't listed. When you add a route to one of these blueprints, add its endpoint to `ENDPOINT_PERMISSIONS` or it will always 403.
  - The convention is: index, view and related tabs need view; create, edit and delete need their own; link and unlink need edit on the record you're on (for example `jobs.link_assets` needs `jobs.edit`).
  - New custom groups start with view on every resource.
  - Any `abort(403)` renders `templates/errors/403.html` through the `errorhandler(403)` in `create_app`. The page says your user group doesn't allow this and links to the dashboard. The suspended-organisation page is a separate, explicit 403 response and isn't affected. Any 404 (unknown URL, `get_or_404`, another organisation's record, `/admin` for non-admins) renders `templates/errors/404.html` the same way, with a dashboard or sign-in button depending on whether you're signed in.
  - Unhandled exceptions render `templates/errors/500.html` (debug off only; debug mode shows Flask's debugger). The handler rolls back the session first. The template is deliberately standalone: it doesn't extend `base.html` or use `url_for`/`g.user`, so it renders even when the database is the problem. Keep it that way.
- **User groups** (`/user-groups`, `user_groups.manage`, so admins only; linked from the navbar dropdown):
  - Index, create, view, edit and delete. Create and edit have a name plus the permissions grid (`user_groups/_permissions_grid.html`, plain `permissions` checkboxes read with `request.form.getlist`).
  - Names are unique per organisation, case-insensitively.
  - The Admin group redirects away from edit and delete. A group with members can't be deleted.
  - People are moved between groups on their person record. The group view lists its members.
- **Auth and tenancy in resource routes**: every resource blueprint calls `<bp>.before_request(require_login)` straight after it's created, so every route needs a signed-in user. Every query goes through `org_query(Model)` or `org_get_or_404(Model, id)` from `app/routes/helpers.py`, which filter by `g.user.organisation_id`. Never use a bare `Model.query` or `Model.query.get_or_404` in a resource route. A record from another organisation gives a 404.
  - Create routes set `organisation_id=g.user.organisation_id`. Edit routes don't need to, because the record was loaded scoped and `organisation_id` is never taken from a form.
  - Site dropdowns are built from `org_query(Site)`, and WTForms rejects any `site_id` not in the choices, so another organisation's site can't be assigned. Related tabs use relationships from an already-scoped parent. Link queries also go through `org_query`.
- Every resource has the standard routes: `index`, `create`, `view`, `edit`, `delete`. Delete is GET for a confirmation page and POST to delete, with a hidden `csrf_token` input.
- Related-data tab routes sit under a `### RELATED DATA ###` comment in each route file. For example `/assets/<id>/jobs` renders `assets/view/jobs.html` with `active_tab="jobs"`.
- **Index pages** paginate with `query.order_by(<field>, Model.id).paginate(page=page, per_page=25, error_out=False)`. The `id` tie-breaker is needed because the sort field isn't unique. Templates import `pagination_nav` from `pagination.html` and show three states: results, "No X match your search." (when a search or site filter is active) and "No X yet."
- **Imports** (`/imports`, `app/routes/imports.py`, `services/imports.py`; "Import data" in the navbar dropdown):
  - Everything here needs the extra permission `imports.manage` ("Import data"). It covers importing every type, whatever else the group allows. Undo also needs delete on that type (`import_type.delete_permission`, e.g. `sites.delete`): the Undo button is hidden and the route gives a 403 without it, so importing doesn't become a back door to deleting records later. Types: sites, assets, people, suppliers, job schedules, in `IMPORT_TYPES`. Each lists `Column`s (key, kind, required, max length, notes); the guidance page and the template CSV (`/imports/new/<slug>/template.csv`) are generated from them.
  - `/imports/new/<slug>` shows the guidance and the upload form (`ImportForm`). `read_csv()` refuses the whole file, with nothing saved and no event, for unknown, duplicate or missing required headers, no rows, more than 5,000 rows or more than 5 MB. It decodes UTF-8 (BOM allowed), falling back to Windows-1252 for Excel.
  - `run_import()` checks every row: parsing (dates dd/mm/yyyy, dd-mm-yyyy or yyyy-mm-dd; money with £ and commas; Yes/No; frequency key or label; site and user group by name), the same uniqueness rules as the forms (asset and serial numbers per organisation, ignoring case; emails across all users) including against earlier rows in the file, and notice days against the frequency. Each row saves in its own savepoint. Failures are recorded on the event, then it redirects to `/imports/<id>`.
  - Imported people get a User with `can_login` False, so nobody is emailed. Only admins can use the Admin group.
  - Import never updates records.
  - **Undo** (GET confirm and POST `/imports/<id>/undo`) deletes the records still left from the import (a person with their user), and sets `undone_at`/`undone_by_id`. The event stays in the history as Undone.
  - `undo_blockers()` refuses undo while deleting would take other data with it, or break the people rules:
    - an imported site has assets, jobs or job schedules
    - an imported schedule has jobs raised from it
    - an imported person can now log in
    - a non-admin is undoing people in the Admin group
  - "View" links to the resource index with `?import_id=`, which each `filtered_<x>()` applies (shown by `_import_filter_notice.html`), so downloads and Columns work on imported records too.
- **Downloads and Columns** (`templates/_table_tools.html`, `app.js`, `services/exports.py`):
  - Every data table has `text-nowrap` inside `table-responsive`, so wide tables scroll sideways rather than squash.
  - A table has `data-columns="<key>"`, and each hideable `<th>` has `data-col="<key>"`. The first column (the one you click through on) also has `data-col-fixed`, and action and checkbox columns have no `data-col`. `{{ table_tools(key, export_endpoint) }}` (import it `with context`) renders a Download dropdown (only when `export_endpoint` is given) and a Columns dropdown (desktop only).
  - `app.js` builds the checkboxes, hides columns by header position and remembers hidden columns per key in localStorage, so the jobs index and jobs tabs share a choice. It adds `?columns=` with the visible keys to the download links when they're clicked.
  - Only the six resource index pages download. Each route file has `<X>_COLUMNS` (key, label, getter; keys must match the `data-col` slugs) and a `filtered_<x>()` function that's shared by `index` and `export` (`/<resource>/export/<csv|pdf>`, endpoint `<bp>.export`, needs view). Index pages pass the endpoint to list partials with `{% set export_endpoint = '...' %}` before the include. Related tabs and link pages get Columns but no Download.
  - `export_response()`: the CSV has a BOM for Excel, raw numbers and `dd-mm-yyyy` dates, and a `'` before values that would run as Excel formulas (CSV injection; phone and number-like values are left alone). The PDF is landscape A4 with repeated headers, page numbers and at most `PDF_MAX_ROWS` (2000) rows; the CSV has no limit. ReportLab's built-in Helvetica can't show characters outside Western European ones.
- **List partials** (`assets/asset_list.html`, `jobs/job_list.html`, `job_schedules/job_schedule_list.html`) are shared by index pages and related tabs. Each has a desktop table (`d-none d-md-block`) and mobile cards (`d-md-none`). The whole row or card is clickable via Bootstrap `stretched-link` on the first cell or card title (`<tr class="position-relative">`). There are no View buttons. Sites and suppliers have their tables inline in `index.html`, using the same pattern.
- **View pages**: each resource with tabs has `templates/<resource>/view/` containing:
  - `_layout.html` extends `base.html`, includes `_header.html` and `_tabs.html`, and defines `{% block tab_content %}`.
  - `overview.html` and one file per related tab, each extending `_layout.html`.
  - Suppliers have no tabs yet, so they still use a flat `suppliers/view.html`.
- **Link/unlink pattern** for the many-to-many relationships. There are 8 routes:
  - `/assets/<id>/jobs/link|unlink`, `/assets/<id>/job-schedules/link|unlink`, `/jobs/<id>/assets/link|unlink`, `/job-schedules/<id>/assets/link|unlink`.
  - GET on link lists same-site, not-yet-linked items. GET on unlink lists the currently linked items.
  - POST reads ticked IDs with `selected_ids('<field>')` from `app/routes/helpers.py`, which parses UUIDs and drops junk. Link only acts on IDs from its own query; unlink only on items actually linked. It then flashes a message and redirects to the tab.
  - Checkbox list partials: `*_select_list.html` (field names `asset_ids`, `job_ids`, `job_schedule_ids`). The pages are `<resource>/view/<related>_link.html` and `_unlink.html`.
  - Tabs show a "Link X" button, plus an "Unlink X" button only when items exist.
- Flash categories map to Bootstrap colours (`success`, `warning`, ...) and render as toasts in `base.html`.
- **Line endings: all source files use CRLF.** Python's `write_text` and Git Bash `sed -i` can silently switch files to LF. After any scripted edit, normalise back to CRLF.
- Style: match the existing code. Plain functions, explicit field assignment in create and edit routes.
- `app/services/` holds plain-function modules for logic that isn't a CRUD route, currently auth, email, permissions, scheduling and jobs. Resource CRUD stays in the routes.
  - `services/email.py`: `send_email(to, subject, html)` sends through Resend. If `RESEND_API_KEY` is empty, it logs the email (code included) instead, which is useful in dev and tests.
  - `services/auth.py`: `create_and_send_otp(email, first_name, welcome=False)` looks up the user, expires their unused OTPs (it doesn't delete them, so the rate limit can count them, and rows older than a day are deleted), saves a new one (expires in 15 minutes) and emails `templates/emails/auth/send_otp.html` with the code and a link to `/auth/validate/<otp_id>`. It returns the OTP. `welcome=True` (registration) and `invited_by="Name"` (people) change the subject and opening line. Every code email links to `/login` for a fresh code.   - Also in `services/auth.py`:
    - `check_otp_rate_limit(user)` returns an error message or None: one code per 60 seconds and 5 per hour, per user, counted from OTP rows in the database. `/login` calls it. Registration doesn't, because a new user has no codes.
    - `verify_otp(otp, code)` returns an error message, or None on success. It rejects users with `can_login` off or a suspended organisation, and codes that are already used, expired or locked (5 wrong attempts) and counts wrong attempts. On success it sets `used_at` and `email_verified_at`.
    - `create_session(user)` saves a `UserSession` and returns the raw token. `set_session_cookie(response, token)` sets the `auth_session` cookie (HttpOnly, SameSite=Lax, 30 days, Secure unless `AUTH_COOKIE_SECURE=false`).
    - `get_active_session(token)` returns the `UserSession`, or None if it's revoked or expired, or the user can't log in or their organisation is suspended. It touches `last_seen_at`.
    - `active_sessions()` is a query for sessions that aren't revoked or expired. `active_session_counts(users)` returns `{user_id: count}`.
    - Revoking: `revoke_session(user_session)`, `revoke_user_sessions(user)`, `revoke_organisation_sessions(org)` and `revoke_all_sessions()`.
    - `suspend_organisation(org)` sets `suspended_at` and revokes the organisation's sessions. `reinstate_organisation(org)` clears it.
    - `clear_session_cookie(response)` deletes the cookie. `as_utc()` normalises SQLite's naive datetimes for comparison.
- `auth` blueprint (`/auth`):
  - Its `before_app_request` sets `g.user_session` and `g.user` from the `auth_session` cookie on every request (both None when signed out).
  - `login()` is mounted at the root `/login` (endpoint `auth.login`) in `create_app`. It sends a code to an existing user and redirects to validate. An unknown email gets a form error, which reveals whether an account exists, as registration already does. A signed-in user is redirected to the dashboard.
  - `login` refuses users with `can_login` off, then checks the rate limit before sending.
  - A user in a suspended organisation gets `auth/organisation_suspended.html` (403) from `organisation_suspended()`, both at `login` and when opening `validate`. It tells them to contact their system admin or `SUPPORT_EMAIL`.
  - `logout` (POST `/auth/logout` only, CSRF-protected) revokes `g.user_session`, clears the cookie and redirects to `/login`.
  - `resend` (POST `/auth/validate/<otp id>/resend`) is the "Send a new code" button on the validate page. It's for an email that never arrived or a code that's expired, used or locked. It sends a new code to the same user (rate-limited, keeps `?next`), then redirects to the new code's validate page. It refuses users with `can_login` off and suspended organisations.
  - An unknown OTP id (codes are deleted after a day, so old email links hit this) on `validate` or `resend` goes to `expired_link()`, which redirects to `/login` with a message instead of a 404.
  - `validate` (GET/POST `/auth/validate/<otp id>`): on a correct code it creates a session, sets the cookie and redirects to the dashboard. On a wrong code it re-renders with the error on the code field.
  - `/auth/sessions` (login required, links from the navbar dropdown):
    - Admins (`sessions.manage`) see "Sessions": every active session in their organisation, with Revoke.
    - Everyone else sees "Your sessions": only their own.
    - POST route: `/auth/sessions/<id>/revoke` (404 outside your scope).
    - There's deliberately no suspend here. An organisation removes someone's access by turning off `can_login` on their person record, and revoking is for signing a single device out, for example a compromised one.
- `admin` blueprint (`/admin`, platform admins only):
  - `before_request(require_platform_admin)` gives a 404 to signed-in non-admins and redirects signed-out visitors to login.
  - Tabs (`admin/_tabs.html`): Organisations (the `/admin/` index), Sessions and Users. All are paginated. Sessions and Users share `admin/_filters.html` (email search and an organisation filter).
  - Organisations: name search, user and active session counts, a Sessions link, and Suspend or Reinstate (POST `/admin/organisations/<id>/suspend|reinstate`). You can't suspend your own organisation.
  - Sessions: revoke one, "Revoke all sessions for <org>" (when filtered; POST `/admin/organisations/<id>/revoke-sessions`) and "Revoke all sessions everywhere" (POST `/admin/sessions/revoke-all`), to force everyone to sign in again after a compromise.
  - Users: "Revoke sessions" per user (POST `/admin/users/<id>/revoke-sessions`).
  - People are never suspended individually. Per-person actions are session revokes only, and access is controlled by `can_login`, which the organisation owns.
- Session actions live in `app/routes/helpers.py`: `revoke_and_redirect` (one session) and `signed_out_or_back(message, fallback)` (after a bulk revoke: it signs you out if your own session went, otherwise goes back), plus `back_to`.
  - Forms post a hidden `next` (`request.full_path`) so the redirect keeps filters and page. It's checked with `safe_next_url`.
  - Revoking your own current session (including revoke-all on your own organisation) signs you out.
  - Partials: `auth/session_list.html` (both areas) and `auth/user_list.html` (`/admin/users`), configured with variables set before the include: `revoke_endpoint`, `revoke_user_endpoint` and `show_organisation`.
- Jinja filters (registered in `create_app`): `datetime` formats a datetime as `21-10-2026 14:05`, `date` formats a date as `21-10-2026`, and `currency` formats a number as `£12,345.60` (money is stored as `Numeric(12, 2)` pounds). All give `-` for None. Times are UTC.
- `parse_uuid(value)` in helpers returns a UUID or None, for ids in query strings. Unlike a raw string, it works on SQLite too.
- `require_login()` / `login_required` (in `app/routes/helpers.py`) redirect to `/login`. GET requests add `?next=<full path>`. POSTs don't, and nothing is changed.
  - `login` passes `next` on to the validate URL, and `validate` redirects to it after a correct code, falling back to the dashboard.
  - `next` is always checked with `safe_next_url()`, which allows only local paths (no `//`, backslashes or other hosts).
  - The validate link in emails has no `next`.
  - The dashboard uses the decorator. Resource blueprints use the hook.
- Navbar: when signed in, a `bi-person-circle` icon opens a dropdown with the name, email and a Sign out button (a POST form). When signed out, it shows a Sign in link.
- `dashboard` blueprint: `/dashboard/` renders `dashboard/index.html`. It's the landing page after login.
  - With `jobs.view`, it shows four job cards: Compliance Status (green tick, or red with the number of overdue statutory jobs), Compliance Risks (statutory, not closed, due today to 7 days ahead), Overdue Jobs and New Jobs.
  - Each card links to `/jobs` with filters that give the same count, so keep the dashboard queries and the `jobs.index` filters in step. Overdue is `overdue_conditions(uk_today())` in `services/jobs.py`, used by both. The jobs Status filter has a filter-only `not_closed` option. `/` (endpoint `dashboard.root`, mounted in `create_app` like `/register` and `/login`) redirects to it.
- Users need a group: call `create_default_groups(org)` and pass `user_group=admin` or `team` when creating a `User` directly, and post `user_group_id` in person forms.
- Testing signed-in routes: create a session with `create_session(user)` inside `app.test_request_context()`, then `client.set_cookie('auth_session', token, domain='localhost')`.
- Testing auth with `app.test_client()`: the auth cookie is Secure, so pass `base_url='https://localhost'` on requests. Flask's own `session` cookie (flash messages, CSRF) is set too, so look for `auth_session` specifically.
- Email templates live in `templates/emails/<area>/` as standalone HTML with inline styles (they don't extend `base.html`).

## Testing

There's no test suite yet. Changes have been checked with throwaway scripts against SQLite, never the dev Postgres database:
- Set `DATABASE_URL=sqlite:///<scratch path>/x.db`. An environment variable overrides `.env`.
- Run `db.create_all()`, then `seeder.py`, then use `app.test_client()` with `WTF_CSRF_ENABLED = False`.
- Postgres UUID columns work on SQLite through SQLAlchemy's generic UUID fallback. One catch: SQLite rejects string IDs assigned to UUID columns (`'str' object has no attribute 'hex'`), for example `site_id=form.site_id.data` in create and edit routes. Postgres accepts them, so treat that error as a test-setup artifact, not an app bug.

## Known gaps / feature backlog

- **Raising jobs from schedules**: `flask create-jobs-for-job-schedules` (`app/commands.py`) calls `create_scheduled_jobs()` in `services/jobs.py`. It's meant to run once a day from a DigitalOcean App Platform cron job.
  - It picks schedules with `next_job_creation_date <= uk_today()`. It uses `<=` so a missed run or a creation date already in the past is still caught. If a schedule is behind, one run raises every occurrence that's due, up to `MAX_JOBS_PER_SCHEDULE_RUN` (366) per schedule, and logs a warning if it's still behind, with the rest raised on the next run.
  - "Today" is the UK date (`Europe/London` via `zoneinfo`, which needs the `tzdata` package on Windows), never the server's UTC date, so BST doesn't shift which day a schedule falls on.
  - `raise_job()` copies summary, work required, statutory compliance, site, organisation and linked assets. It sets `due_by` from `next_due_by`, `job_schedule_id`, status `new` and source `planned`. Then it moves `next_due_by` on with `next_due_date()` (calendar months and years; the 31st clamps to a shorter month's end) and recalculates `next_job_creation_date`.
  - Each schedule is locked (`FOR UPDATE SKIP LOCKED`), rechecked and committed on its own. A failure is rolled back and logged, the rest carry on, and the command exits non-zero at the end. `JobForm` deliberately has no job schedule field, so don't add one.
- `Supplier` isn't related to anything except its organisation yet (for example, a supplier assigned to a job). It has no tabs.
- Registration, OTP validation, sessions, rate limiting, suspension and `/admin` work. Still missing:
  - `organisation.manage` exists but there are no organisation settings routes yet
  - record-level rules, for example engineers only seeing jobs assigned to them
  - an IP-based limit on `/login` (the per-user limit doesn't stop one client trying many emails), for example Flask-Limiter with Redis
- `DO_SPACES_*` config suggests file uploads (DigitalOcean Spaces) are planned.
- No automated tests.

## Deferred: design (decided October 2026)

The user plans to keep server-rendered Bootstrap and polish it later rather than switch stacks. When that work starts:
1. Pull repeated markup (list tables and cards, page headers, form fields, empty states, link/unlink pages) into Jinja macros.
2. Move Bootstrap from the CDN to a Sass build, or an `app.css` that overrides Bootstrap's CSS variables, for brand tokens.
3. Settle the overall layout (navigation, page headers, form and detail layouts).
4. Add htmx or Alpine.js for interactivity, and isolated JS widgets for things like a scheduling calendar.
