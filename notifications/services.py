"""Who gets told what. Task code calls the event functions below; nothing else writes notifications."""
from django.core.cache import cache
from django.utils import timezone

from .models import Notification

Kind = Notification.Kind


def _label(task):
    title = task.title if len(task.title) <= 60 else task.title[:57] + "..."
    return f"{task.display_id} \"{title}\""


def _date(value):
    return value.strftime("%d %b %Y") if value else "no date"


def _unique(users):
    seen, out = set(), []
    for u in users:
        if u is not None and u.pk not in seen:
            seen.add(u.pk)
            out.append(u)
    return out


def task_managers(task):
    """Managers responsible for a task: the one who assigned it and the project's manager."""
    people = [task.assigned_by]
    if task.project_id:
        people.append(task.project.manager)
    return [u for u in _unique(people) if u.is_manager_role and u.is_active]


def notify(recipient, kind, message, task=None, actor=None):
    """Create one notification. Nobody is notified about their own action, or while deactivated."""
    if recipient is None or not recipient.is_active or (actor is not None and recipient.pk == actor.pk):
        return None
    return Notification.objects.create(recipient=recipient, kind=kind, message=message[:300], task=task)


def _name(user):
    return user.display_name if user else "Someone"


# ------------------------------------------------------------------ events
def task_assigned(task, actor):
    due = f" Due {_date(task.est_date)}." if task.est_date else ""
    notify(task.assigned_to, Kind.ASSIGNED, f"{_name(actor)} assigned you {_label(task)}.{due}", task, actor)


def task_reassigned(task, old_employee, actor):
    notify(task.assigned_to, Kind.ASSIGNED, f"{_name(actor)} assigned you {_label(task)}.", task, actor)
    notify(old_employee, Kind.REASSIGNED, f"{_label(task)} was moved to {task.assigned_to.display_name}.", task, actor)


def status_changed(task, old_status, actor, remarks=""):
    """Employee moved a task to review/blocked -> tell the managers. Anyone else changed it -> tell the employee."""
    new = task.status
    S = task.Status
    if actor is not None and actor.pk == task.assigned_to_id:
        if new == S.PENDING_REVIEW:
            for m in task_managers(task):
                notify(m, Kind.SUBMITTED, f"{_name(actor)} submitted {_label(task)} for review.", task, actor)
        elif new == S.BLOCKED:
            for m in task_managers(task):
                notify(m, Kind.BLOCKED, f"{_name(actor)} marked {_label(task)} as blocked.", task, actor)
        return
    extra = f" Feedback: {remarks}" if remarks else ""
    if old_status == S.PENDING_REVIEW and new == S.COMPLETED:
        notify(task.assigned_to, Kind.APPROVED, f"{_label(task)} was approved and completed.{extra}", task, actor)
    elif old_status == S.PENDING_REVIEW and new == S.IN_PROGRESS:
        notify(task.assigned_to, Kind.REWORK, f"{_label(task)} was sent back for rework.{extra}", task, actor)
    else:
        old = S(old_status).label if old_status else "New"
        notify(task.assigned_to, Kind.STATUS,
               f"{_name(actor)} changed {_label(task)} from {old} to {task.get_status_display()}.{extra}", task, actor)


def deadline_changed(task, old_date, new_date, actor):
    notify(task.assigned_to, Kind.DEADLINE,
           f"Deadline of {_label(task)} changed from {_date(old_date)} to {_date(new_date)}.", task, actor)


def comment_added(task, comment):
    body = comment.body.strip() or "(attached documents)"
    snippet = body if len(body) <= 80 else body[:77] + "..."
    who = _name(comment.author)
    for person in _unique([task.assigned_to] + task_managers(task)):
        notify(person, Kind.COMMENT, f"{who} commented on {_label(task)}: {snippet}", task, comment.author)


# ------------------------------------------------------------------ overdue
def notify_overdue():
    """Tell the employee and their managers about every overdue task, once per task and deadline.
    Safe to run repeatedly (cron, or lazily from the dashboard)."""
    from tasks.models import Task

    tasks = list(Task.objects.overdue().filter(is_archived=False)
                 .select_related("assigned_to", "assigned_by", "project__manager"))
    if not tasks:
        return 0
    existing = set(Notification.objects.filter(kind=Kind.OVERDUE).values_list("recipient_id", "dedupe_key"))
    fresh = []
    for t in tasks:
        key = f"overdue:{t.pk}:{t.est_date.isoformat()}"
        due = _date(t.est_date)
        targets = []
        if t.status != Task.Status.PENDING_REVIEW:  # waiting on the manager, so the employee is not the blocker
            targets.append((t.assigned_to, f"{_label(t)} is overdue. It was due {due}."))
        for m in task_managers(t):
            targets.append((m, f"{_label(t)} assigned to {t.assigned_to.display_name} is overdue. It was due {due}."))
        for person, message in targets:
            if person.is_active and (person.pk, key) not in existing:
                existing.add((person.pk, key))
                fresh.append(Notification(recipient=person, kind=Kind.OVERDUE, message=message[:300],
                                          task=t, dedupe_key=key))
    Notification.objects.bulk_create(fresh, ignore_conflicts=True)
    return len(fresh)


def sweep_overdue_if_due(every_seconds=1800):
    """Run the overdue check at most once per interval per process, so a busy site is not slowed down."""
    if cache.add("notifications:overdue-sweep", timezone.now().isoformat(), every_seconds):
        return notify_overdue()
    return 0
