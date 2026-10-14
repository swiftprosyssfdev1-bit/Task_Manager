"""Analytics calculations.

Definitions used everywhere (and shown on the screens):
- Calendar days    : completed date minus assign date. Same-day completion is 0. Never negative.
- Business days    : working days after the assign date up to and including the completed date.
                     Saturdays/Sundays (``settings.WEEKEND_DAYS``) and rows in the Holiday table are skipped.
                     Same-day completion is 0.
- Working hours    : the sum of work-log durations. This is effort, not elapsed time.
- Est. duration    : est. date minus assign date. Tasks without an est. date have no estimate, so they are
                     left out of on-time, delay and variance figures instead of being counted as late or early.
- On time          : completed date on or before the est. date.
- Original deadline: the est. date before the first recorded deadline change. Reported next to the current
                     deadline so approved date changes stay visible.
"""
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.db.models import Count, DecimalField, Exists, Min, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from accounts.models import User
from tasks.models import (Task, TaskAssignmentHistory, TaskDeadlineChange, TaskStatusHistory,
                          TaskWorkLog)

from .models import Holiday

STATUS_HEX = {
    Task.Status.NOT_STARTED: "#6c757d", Task.Status.IN_PROGRESS: "#28a8e0", Task.Status.ON_HOLD: "#ffc107",
    Task.Status.BLOCKED: "#dc3545", Task.Status.PENDING_REVIEW: "#0dcaf0", Task.Status.COMPLETED: "#198754",
    Task.Status.CANCELLED: "#212529",
}
HOURS_FIELD = DecimalField(max_digits=8, decimal_places=2)


# ---------------------------------------------------------------- date maths
def weekend_days():
    return tuple(getattr(settings, "WEEKEND_DAYS", (5, 6)))


def workload_threshold():
    return int(getattr(settings, "WORKLOAD_WARNING_OPEN_TASKS", 10))


def get_holidays():
    return frozenset(Holiday.objects.values_list("date", flat=True))


def calendar_days(start, end):
    return max((end - start).days, 0)


def business_days(start, end, holidays=frozenset()):
    """Working days in (start, end]. Same day, or end before start, gives 0."""
    if end <= start:
        return 0
    weekend = weekend_days()
    count, day = 0, start + timedelta(days=1)
    while day <= end:
        if day.weekday() not in weekend and day not in holidays:
            count += 1
        day += timedelta(days=1)
    return count


@dataclass(frozen=True)
class Durations:
    actual_calendar: int
    actual_business: int
    estimated: int | None
    delay_days: int | None
    days_early: int | None
    variance: int | None
    on_time: bool | None


def durations(assign_date, est_date, completed_date, holidays=frozenset()):
    actual = calendar_days(assign_date, completed_date)
    business = business_days(assign_date, completed_date, holidays)
    if est_date is None:
        return Durations(actual, business, None, None, None, None, None)
    estimated = calendar_days(assign_date, est_date)
    return Durations(
        actual_calendar=actual, actual_business=business, estimated=estimated,
        delay_days=max((completed_date - est_date).days, 0),
        days_early=max((est_date - completed_date).days, 0),
        variance=actual - estimated,
        on_time=completed_date <= est_date)


# ---------------------------------------------------------------- per-task metrics
def annotate_tasks(qs):
    """Adds hours (sum of work logs) and the context flags used to read the numbers fairly."""
    first_change = TaskDeadlineChange.objects.filter(task=OuterRef("pk")).order_by("changed_at", "id")
    logs = (TaskWorkLog.objects.filter(task=OuterRef("pk")).order_by().values("task")
            .annotate(total=Sum("duration_hours")).values("total"))
    return qs.annotate(
        hours=Coalesce(Subquery(logs, output_field=HOURS_FIELD), Value(Decimal("0.00")), output_field=HOURS_FIELD),
        deadline_revised=Exists(first_change),
        original_est=Subquery(first_change.values("old_date")[:1]),
        reassigned=Exists(TaskAssignmentHistory.objects.filter(task=OuterRef("pk"), from_employee__isnull=False)),
        was_blocked=Exists(TaskStatusHistory.objects.filter(task=OuterRef("pk"), to_status=Task.Status.BLOCKED)),
    )


@dataclass(frozen=True)
class TaskMetrics:
    task: Task
    d: Durations
    hours: Decimal
    deadline_revised: bool
    reassigned: bool
    was_blocked: bool
    on_time_vs_original: bool | None


def task_metrics(task, holidays=frozenset()):
    """``task`` must come from annotate_tasks() and be completed."""
    original = task.original_est if task.deadline_revised else task.est_date
    return TaskMetrics(
        task=task, d=durations(task.assign_date, task.est_date, task.completed_date, holidays),
        hours=task.hours, deadline_revised=task.deadline_revised, reassigned=task.reassigned,
        was_blocked=task.was_blocked,
        on_time_vs_original=None if original is None else task.completed_date <= original)


def completed_metrics(qs, start=None, end=None, holidays=frozenset()):
    qs = qs.filter(status=Task.Status.COMPLETED, completed_date__isnull=False)
    if start:
        qs = qs.filter(completed_date__gte=start)
    if end:
        qs = qs.filter(completed_date__lte=end)
    return [task_metrics(t, holidays) for t in annotate_tasks(qs.select_related("project__department", "assigned_to"))
            .order_by("-completed_date", "-id")]


# ---------------------------------------------------------------- employee statistics
def _avg(total, n, places=1):
    return round(total / n, places) if n else None


def _pct(part, whole):
    return round(100 * part / whole, 1) if whole else None


def _blank_row(user):
    return dict(user=user, assigned=0, cohort=0, cohort_done=0, completed=0, on_time=0, late=0, no_estimate=0,
                on_time_orig=0, late_orig=0, cal_sum=0, bus_sum=0, hours_sum=Decimal("0"),
                open_now=0, in_progress_now=0, pending_now=0, overdue_now=0, blocked_now=0, review_now=0,
                logged_hours=Decimal("0"))


def employee_stats(tasks, start, end, holidays=frozenset(), today=None, ensure_users=()):
    """One row per employee for the period [start, end].

    Period figures : assigned (assign date in period), completed (completed date in period), logged hours.
    Completion rate: of tasks assigned in the period (cancelled excluded), the share now completed.
    Snapshot       : open / in progress / pending / overdue / blocked / in review are as of today.
    Pending        : open tasks that are not In Progress (Not Started, On Hold, Blocked, Pending Review).
    ensure_users   : users who must get a row even when they have no activity (individual reports).
    """
    today = today or timezone.localdate()
    base = annotate_tasks(tasks.select_related("assigned_to"))
    rows = {}

    def row(user):
        return rows.setdefault(user.pk, _blank_row(user))

    for t in base.filter(assign_date__range=(start, end)):
        r = row(t.assigned_to)
        r["assigned"] += 1
        if t.status != Task.Status.CANCELLED:
            r["cohort"] += 1
            r["cohort_done"] += t.status == Task.Status.COMPLETED

    for t in base.filter(status=Task.Status.COMPLETED, completed_date__range=(start, end)):
        m, r = task_metrics(t, holidays), row(t.assigned_to)
        r["completed"] += 1
        r["cal_sum"] += m.d.actual_calendar
        r["bus_sum"] += m.d.actual_business
        r["hours_sum"] += m.hours
        if m.d.on_time is None:
            r["no_estimate"] += 1
        elif m.d.on_time:
            r["on_time"] += 1
        else:
            r["late"] += 1
        if m.on_time_vs_original is True:
            r["on_time_orig"] += 1
        elif m.on_time_vs_original is False:
            r["late_orig"] += 1

    for t in base.filter(status__in=Task.OPEN_STATUSES):
        r = row(t.assigned_to)
        r["open_now"] += 1
        r["overdue_now"] += t.is_overdue
        if t.status == Task.Status.IN_PROGRESS:
            r["in_progress_now"] += 1
        else:
            r["pending_now"] += 1
        r["blocked_now"] += t.status == Task.Status.BLOCKED
        r["review_now"] += t.status == Task.Status.PENDING_REVIEW

    hours = (TaskWorkLog.objects.filter(task__in=tasks.values("pk"), work_date__range=(start, end))
             .order_by().values("employee_id").annotate(total=Sum("duration_hours")))
    hours = {h["employee_id"]: h["total"] for h in hours}
    missing = [pk for pk in hours if pk not in rows]
    for user in User.objects.filter(pk__in=missing):
        row(user)
    for user in ensure_users:
        row(user)
    threshold = workload_threshold()
    for pk, r in rows.items():
        r["logged_hours"] = hours.get(pk) or Decimal("0")
        r["completion_rate"] = _pct(r["cohort_done"], r["cohort"])
        r["on_time_pct"] = _pct(r["on_time"], r["on_time"] + r["late"])
        r["on_time_orig_pct"] = _pct(r["on_time_orig"], r["on_time_orig"] + r["late_orig"])
        r["avg_calendar"] = _avg(r["cal_sum"], r["completed"])
        r["avg_business"] = _avg(r["bus_sum"], r["completed"])
        r["avg_hours"] = _avg(r["hours_sum"], r["completed"], 2)
        r["high_workload"] = r["open_now"] >= threshold
    return sorted(rows.values(), key=lambda r: (r["user"].first_name.lower(), r["user"].username.lower()))


# ---------------------------------------------------------------- project statistics
def project_stats(tasks, today=None):
    today = today or timezone.localdate()
    S = Task.Status
    rows = (tasks.order_by().values("project_id", "project__name", "project__code")
            .annotate(total=Count("pk"),
                      completed=Count("pk", filter=Q(status=S.COMPLETED)),
                      cancelled=Count("pk", filter=Q(status=S.CANCELLED)),
                      open=Count("pk", filter=Q(status__in=Task.OPEN_STATUSES)),
                      blocked=Count("pk", filter=Q(status=S.BLOCKED)),
                      overdue=Count("pk", filter=Q(status__in=Task.OPEN_STATUSES, est_date__lt=today)))
            .order_by("project__name"))
    plan = defaultdict(lambda: [0, 0, 0])  # estimated days, actual days, tasks with an estimate
    for pid, assign, est, done in tasks.filter(status=S.COMPLETED, est_date__isnull=False,
                                              completed_date__isnull=False).values_list(
            "project_id", "assign_date", "est_date", "completed_date"):
        p = plan[pid]
        p[0] += calendar_days(assign, est)
        p[1] += calendar_days(assign, done)
        p[2] += 1
    result = []
    for r in rows:
        est, act, n = plan.get(r["project_id"], [0, 0, 0])
        result.append(dict(
            id=r["project_id"], name=r["project__name"] or "No project", code=r["project__code"] or "",
            total=r["total"], completed=r["completed"], cancelled=r["cancelled"], open=r["open"],
            blocked=r["blocked"], overdue=r["overdue"],
            completion_pct=_pct(r["completed"], r["total"] - r["cancelled"]),
            avg_estimated=_avg(est, n), avg_actual=_avg(act, n)))
    return result


# ---------------------------------------------------------------- dashboard data
def status_breakdown(tasks):
    counts = {r["status"]: r["n"] for r in tasks.order_by().values("status").annotate(n=Count("pk"))}
    items = [(s, counts.get(s, 0)) for s, _ in Task.Status.choices]
    return {"labels": [Task.Status(s).label for s, n in items if n],
            "data": [n for s, n in items if n],
            "colors": [STATUS_HEX[s] for s, n in items if n]}


def completion_trend(tasks, today=None, weeks=8, months=6):
    today = today or timezone.localdate()
    monday = today - timedelta(days=today.weekday())
    first_week = monday - timedelta(weeks=weeks - 1)
    y, m = today.year, today.month - (months - 1)
    while m < 1:
        y, m = y - 1, m + 12
    first_month = date(y, m, 1)
    dates = list(tasks.filter(status=Task.Status.COMPLETED, completed_date__gte=min(first_week, first_month))
                 .values_list("completed_date", flat=True))
    wk = Counter((d - first_week).days // 7 for d in dates if d >= first_week)
    mo = Counter((d.year, d.month) for d in dates if d >= first_month)
    month_keys = [(first_month.year + (first_month.month - 1 + i) // 12, (first_month.month - 1 + i) % 12 + 1)
                  for i in range(months)]
    return {
        "weekly": {"labels": [(first_week + timedelta(weeks=i)).strftime("%d %b") for i in range(weeks)],
                   "data": [wk.get(i, 0) for i in range(weeks)]},
        "monthly": {"labels": [date(y, m, 1).strftime("%b %y") for y, m in month_keys],
                    "data": [mo.get(k, 0) for k in month_keys]},
    }


def estimate_vs_actual(tasks, limit=10):
    rows = list(tasks.filter(status=Task.Status.COMPLETED, completed_date__isnull=False, est_date__isnull=False)
                .order_by("-completed_date", "-id")[:limit])[::-1]
    return {"labels": [t.display_id for t in rows],
            "estimated": [calendar_days(t.assign_date, t.est_date) for t in rows],
            "actual": [calendar_days(t.assign_date, t.completed_date) for t in rows]}


def project_progress(tasks, limit=8):
    rows = sorted(project_stats(tasks), key=lambda r: -r["open"])[:limit]
    return {"labels": [r["name"] for r in rows],
            "completed": [r["completed"] for r in rows],
            "open": [r["open"] for r in rows]}


def workload(tasks, today=None):
    today = today or timezone.localdate()
    rows = (tasks.filter(status__in=Task.OPEN_STATUSES).order_by()
            .values("assigned_to", "assigned_to__first_name", "assigned_to__last_name", "assigned_to__username")
            .annotate(open=Count("pk"), overdue=Count("pk", filter=Q(est_date__lt=today))))
    out = []
    for r in rows:
        name = f"{r['assigned_to__first_name']} {r['assigned_to__last_name']}".strip() or r["assigned_to__username"]
        out.append((name, r["open"], r["overdue"]))
    out.sort(key=lambda x: -x[1])
    return {"labels": [o[0] for o in out], "open": [o[1] - o[2] for o in out], "overdue": [o[2] for o in out]}


def _brief(qs, n=6):
    return list(qs.select_related("project__department", "assigned_to")[:n])


def project_cards(user, today, limit=6):
    """Active projects the user can see, with progress, technologies, team and how long they have been running."""
    from projects.models import Project
    live = Q(tasks__is_archived=False)
    qs = (Project.objects.visible_to(user).filter(is_active=True)
          .annotate(task_total=Count("tasks", filter=live, distinct=True),
                    task_done=Count("tasks", filter=live & Q(tasks__status=Task.Status.COMPLETED), distinct=True),
                    first_assign=Min("tasks__assign_date"))
          .select_related("manager", "department").prefetch_related("technologies", "memberships__user").order_by("name")[:limit])
    cards = []
    for p in qs:
        begun = p.start_date or p.first_assign
        team = [m.user for m in p.memberships.all()]
        p.percent = round(100 * p.task_done / p.task_total) if p.task_total else 0
        p.days_running = max(1, (today - begun).days + 1) if begun and begun <= today else None
        p.team_list = team
        p.team_size = len(team)
        cards.append(p)
    return cards


def today_tasks(tasks, today, limit=8):
    """Tasks assigned today plus open tasks due today."""
    qs = tasks.filter(Q(assign_date=today) | Q(est_date=today, status__in=Task.OPEN_STATUSES))
    return list(qs.select_related("project__department", "assigned_to").order_by("status", "-priority", "id")[:limit])


def dashboard_data(user, today=None):
    today = today or timezone.localdate()
    S = Task.Status
    tasks = Task.objects.visible_to(user).filter(is_archived=False)
    open_qs = tasks.filter(status__in=Task.OPEN_STATUSES)
    overdue_qs = tasks.overdue().order_by("est_date")
    month_start = today.replace(day=1)
    charts = {"workload": None}  # the dashboard only draws the manager workload chart
    if user.can_manage_tasks:
        cards = [("Assigned this month", tasks.filter(assign_date__gte=month_start).count(), "bi-clipboard-plus", "primary"),
                 ("Completed this month", tasks.filter(status=S.COMPLETED, completed_date__gte=month_start).count(),
                  "bi-check2-circle", "success"),
                 ("In progress", tasks.filter(status=S.IN_PROGRESS).count(), "bi-arrow-repeat", "warning"),
                 ("Overdue", overdue_qs.count(), "bi-exclamation-triangle", "danger"),
                 ("Awaiting review", tasks.filter(status=S.PENDING_REVIEW).count(), "bi-hourglass-split", "info")]
        side = [("Awaiting review", _brief(tasks.filter(status=S.PENDING_REVIEW).order_by("updated_at"))),
                ("Overdue", _brief(overdue_qs))]
        charts["workload"] = workload(tasks, today)
    else:
        hours = TaskWorkLog.objects.filter(employee=user).aggregate(t=Sum("duration_hours"))["t"] or Decimal("0")
        cards = [("My tasks", tasks.exclude(status=S.CANCELLED).count(), "bi-list-task", "primary"),
                 ("Due today", open_qs.filter(est_date=today).count(), "bi-calendar-event", "warning"),
                 ("Due in 7 days", open_qs.filter(est_date__gt=today, est_date__lte=today + timedelta(days=7)).count(),
                  "bi-calendar-week", "secondary"),
                 ("In progress", tasks.filter(status=S.IN_PROGRESS).count(), "bi-arrow-repeat", "primary"),
                 ("Completed", tasks.filter(status=S.COMPLETED).count(), "bi-check2-circle", "success"),
                 ("Overdue", overdue_qs.count(), "bi-exclamation-triangle", "danger"),
                 ("Hours logged", f"{hours:.2f}", "bi-clock-history", "info")]
        upcoming = open_qs.filter(est_date__gte=today, est_date__lte=today + timedelta(days=7)).order_by("est_date")
        side = [("Upcoming deadlines", _brief(upcoming)), ("Overdue", _brief(overdue_qs))]
    return {"cards": cards, "side_lists": side, "charts": charts,
            "project_cards": project_cards(user, today), "today_list": today_tasks(tasks, today)}
