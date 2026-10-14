# Employee Task Manager (Django + MySQL)

Phase 1: authentication, roles (Admin / Manager / Employee), projects, and task assignment.
Phase 2: status history, work logs, comments and manager review, technologies, deadline-change history.
Phase 3: analytics service, holiday calendar, Chart.js dashboards, team analytics and individual employee reports.
Later phases (reports/exports, notifications) build on these models.

## What is included in Phase 1
- Custom user model with roles; login, logout, password reset (console email in development)
- Admin: manage employees (add, edit, deactivate), projects, project teams
- Manager: assign, edit, reassign, filter, search and archive tasks (assignment history is kept)
- Employee: sees only their own tasks; can update status, backup and remarks only
- `REQUIRE_MANAGER_REVIEW` setting: employees submit "Pending Review" instead of "Completed"
- Server-side visibility rules in one place: `Task.objects.visible_to(user)`
- Tests for permissions, visibility, assignment history and date/status rules

## Windows setup
1. Install Python 3.11+ and MySQL 8. Create the database (MySQL Workbench or the `mysql` shell):
   ```sql
   CREATE DATABASE task_manager CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
   ```
2. In the project folder (Command Prompt or PowerShell):
   ```
   python -m venv venv
   venv\Scripts\activate
   pip install -r requirements.txt
   copy .env.example .env
   ```
   If `mysqlclient` fails to install, run `pip install PyMySQL` instead. The project falls back to it automatically.
3. Edit `.env` with your MySQL user and password, and set a new `DJANGO_SECRET_KEY`.
4. Create the tables and the first admin:
   ```
   python manage.py migrate
   python manage.py createsuperuser
   python manage.py runserver
   ```
   Open http://127.0.0.1:8000/ and sign in. Superusers always get the Admin role.
5. As admin, in this order:
   1. **Departments** > Add department.
   2. **Managers** > Add manager, and pick the manager's department.
   3. **Employees** > Add employee, and pick the employee's department and the manager they report to (the manager must belong to that department).
6. Sign in as a manager: create projects (they belong to the manager's department), pick the team from your own employees, and assign tasks to them.
7. Sign in as an employee: open assigned tasks, change status and log progress/work.

## Who can do what
- **Admin:** creates departments, managers and employees; assigns each employee a department and a manager; can move an employee to another manager, or a manager to another department (their team moves with them). View-only on projects and tasks.
- **Manager:** sees only the employees assigned to them (read-only list, with each employee's report). Cannot create departments, managers or employees. Creates projects and tasks, and can only use their own employees as team members and assignees.
- **Employee:** works on tasks assigned to them.
- Technologies are manager-managed records (the admin does not see them).

## Rules
- A manager's department and projects: projects always belong to the manager's department. A manager with no department cannot create projects until the admin assigns one (Managers > Edit).
- An employee with no manager is invisible to every manager. The Employees page shows them with a "Not assigned" badge so the admin can fix them.
- Upgrading: `python manage.py migrate` adds `department` and `manager` to users. Departments are filled from existing projects, and an employee with a department is attached to the first manager of that department. Anyone left blank needs assigning on the Employees page.

## What Phase 2 adds
- Status history for every change (previous status, new status, who, when, note); backfilled for Phase 1 tasks by migration 0003
- Work logs: date, optional start/end time, hours, work performed, technologies, remarks, attachment. Task total hours is always summed from logs
- Validation: hours between 0 and 24, end after start, no future dates, no more than 24 h per employee per day, duplicate entries blocked, attachment type and size limits (`ALLOWED_UPLOAD_EXTENSIONS`, `MAX_UPLOAD_MB`)
- Managers correct work logs with a required reason; each changed field is saved in an audit trail
- Attachments are stored in `media/` and downloaded only through an access-checked view. Do not expose `media/` in your web server config
- Manager review of Pending Review tasks (approve or send back with feedback), plus comments on tasks
- Technologies are manager-managed records (the admin does not see them); employees pick them on tasks and work logs
- Deadline-change history (old date, new date, reason) so analytics can compare against approved dates

## What Phase 3 adds
- `analytics` app. All calculations live in `analytics/services.py`, so dashboards, reports and later exports share one set of rules
- Dashboards: role-specific cards, status doughnut, weekly/monthly completed bar chart, estimated vs actual duration, project progress, and (managers/admin) employee workload
- Analytics page (Admin/Manager): per-employee and per-project statistics for a chosen period, filterable by project and employee. Defaults to the current month
- Employee report: tasks assigned/completed, completion rate, on-time %, average calendar and business days, average hours per task, logged hours, task-wise estimated vs actual, technologies, workload and task history. Employees see only their own report; managers only employees in their scope
- Holidays: add rows under **Analytics > Holidays** in the Django admin (`/admin/`). They are skipped in business-day counts
- Settings: `WEEKEND_DAYS` (default Saturday, Sunday) and `WORKLOAD_WARNING_OPEN_TASKS` (default 10, also an env variable) in `config/settings.py`

### How the numbers are defined
- **Calendar days**: completed date minus assign date. Completing on the assign date is 0. Never negative
- **Business days**: working days after the assign date up to and including the completed date, skipping weekends and holidays
- **Working hours**: sum of work logs (effort, separate from elapsed days)
- **Est. duration**: est. date minus assign date. Tasks with no est. date are left out of on-time, delay and variance figures
- **Delay / early / variance**: `max(0, completed - est)`, `max(0, est - completed)`, `actual - estimated`
- **On time**: completed on or before the est. date. The employee report also shows on-time against the original date when the deadline was changed
- **Completion rate**: tasks assigned in the period that are now completed, cancelled excluded
- **Context badges** on the employee report: date revised, reassigned, was blocked. Ranking people by task counts alone is deliberately not offered

## Running tests
Tests run against SQLite so they need no MySQL setup:
```
set DB_ENGINE=sqlite
python manage.py test
```
(PowerShell: `$env:DB_ENGINE="sqlite"`)

## Deployment notes (preview)
Set `DJANGO_DEBUG=False`, a strong `DJANGO_SECRET_KEY`, real `DJANGO_ALLOWED_HOSTS`, and SMTP email settings.
Run `python manage.py collectstatic` and serve with a WSGI server (waitress works on Windows) behind IIS or nginx with HTTPS.

## Coming next
- Phase 4: Excel/CSV/PDF reports (the nine required reports, built on `analytics/services.py`), in-app notifications and de-duplicated deadline reminders
- Phase 5: sample data command, deployment guide

## Notifications
Managers and employees get in-app notifications (sidebar > Notifications, with an unread badge). The admin is view-only and gets none.

| Event | Who is told |
|---|---|
| Task assigned or reassigned | the employee (and the previous employee, if moved) |
| Employee submits a task for review, or marks it Blocked | the managers responsible for the task |
| Manager approves, sends back for rework, or changes status | the employee (feedback text included) |
| Deadline changed | the employee |
| New comment | the other people on the task (never the author) |
| Task overdue | the employee and the managers, once per task and deadline |

- Overdue alerts are created when someone opens the dashboard or Notifications page (at most once every 30 minutes per server process). To get them even when nobody is logged in, schedule the command daily:
  `0 8 * * * cd /path/to/taskmanager && python manage.py notify_overdue`
- Deactivated users are never notified. Run `python manage.py migrate` to create the new table.
