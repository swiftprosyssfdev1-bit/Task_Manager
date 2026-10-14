"""Business logic for task changes. Views call these so history is written in one place."""
from django.db import transaction

from accounts.models import User
from notifications import services as notify

from .models import Task, TaskAssignmentHistory, TaskDeadlineChange, TaskStatusHistory


@transaction.atomic
def change_status(task, new_status, user, remarks=""):
    """Explicit status transition (used by manager review). Returns the history row, or None if unchanged."""
    old = task.status
    if old == new_status:
        return None
    task.status = new_status
    task.save()
    row = TaskStatusHistory.objects.create(
        task=task, from_status=old, to_status=new_status, changed_by=user, remarks=remarks)
    notify.status_changed(task, old, user, remarks)
    return row


@transaction.atomic
def record_task_created(task, user, note=""):
    TaskStatusHistory.objects.create(task=task, from_status="", to_status=task.status,
                                     changed_by=user, remarks=note)
    TaskAssignmentHistory.objects.create(task=task, from_employee=None, to_employee=task.assigned_to,
                                         changed_by=user)
    notify.task_assigned(task, user)


@transaction.atomic
def record_form_changes(task, form, user):
    """After a task form has saved, log status, assignee and deadline changes it made."""
    note = form.cleaned_data.get("change_note", "")
    changed = form.changed_data
    if "status" in changed:
        TaskStatusHistory.objects.create(
            task=task, from_status=form.initial.get("status") or "", to_status=task.status,
            changed_by=user, remarks=note)
        notify.status_changed(task, form.initial.get("status") or "", user, note)
    if "assigned_to" in changed:
        old_id = form.initial.get("assigned_to")
        TaskAssignmentHistory.objects.create(
            task=task, from_employee_id=old_id, to_employee=task.assigned_to, changed_by=user)
        notify.task_reassigned(task, User.objects.filter(pk=old_id).first(), user)
    if "est_date" in changed:
        TaskDeadlineChange.objects.create(
            task=task, old_date=form.initial.get("est_date"), new_date=task.est_date,
            changed_by=user, reason=note)
        notify.deadline_changed(task, form.initial.get("est_date"), task.est_date, user)
