import os

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from accounts.permissions import MANAGER_ONLY, RoleRequiredMixin, TitleMixin
from notifications import services as notifications

from . import services
from .forms import (CommentForm, ReviewForm, TaskCreateForm, TaskEmployeeForm, TaskFilterForm, TaskForm, TechnologyForm,
                    WorkLogCorrectionForm, WorkLogForm)
from projects.models import Project

from .models import (Task, TaskAttachment, TaskComment, TaskCommentAttachment, TaskWorkLog, TaskWorkLogAudit,
                     Technology)
from projects.models import ProjectAttachment


MISSING_FILE_MESSAGE = ("This file is missing from the server's media folder, so it cannot be downloaded. "
                        "Ask the person to upload it again.")


# ---------------------------------------------------------------- tasks
class TaskListView(LoginRequiredMixin, ListView):
    template_name = "tasks/task_list.html"
    context_object_name = "tasks"
    paginate_by = 20

    def get_filter_form(self):
        return TaskFilterForm(self.request.GET or None, user=self.request.user)

    def get_queryset(self):
        qs = (Task.objects.visible_to(self.request.user).filter(is_archived=False)
              .select_related("project__department", "assigned_to").prefetch_related("technologies").with_hours())
        form = self.get_filter_form()
        order = "-created_at"
        if form.is_valid():
            d = form.cleaned_data
            if d.get("q"):
                qs = qs.filter(Q(title__icontains=d["q"]) | Q(description__icontains=d["q"]))
            if d.get("employee"):
                qs = qs.filter(assigned_to=d["employee"])
            if d.get("project"):
                qs = qs.filter(project=d["project"])
            if d.get("status"):
                qs = qs.filter(status=d["status"])
            if d.get("priority"):
                qs = qs.filter(priority=d["priority"])
            if d.get("overdue"):
                qs = qs.overdue()
            order = d.get("ordering") or order
        return qs.order_by(order, "-id")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["filter_form"] = self.get_filter_form()
        params = self.request.GET.copy()
        params.pop("page", None)
        ctx["querystring"] = params.urlencode()
        return ctx


class TaskDetailView(LoginRequiredMixin, DetailView):
    template_name = "tasks/task_detail.html"
    context_object_name = "task"

    def get_queryset(self):
        return Task.objects.visible_to(self.request.user).select_related(
            "project", "assigned_to", "assigned_by").prefetch_related("technologies")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        task, user = self.object, self.request.user
        awaiting_review = user.can_assign_tasks and task.status == Task.Status.PENDING_REVIEW
        ctx.update(
            assignment_history=task.assignment_history.select_related("from_employee", "to_employee", "changed_by"),
            status_history=task.status_history.select_related("changed_by"),
            deadline_changes=task.deadline_changes.select_related("changed_by"),
            worklogs=task.work_logs.select_related("employee").prefetch_related("technologies"),
            worklog_audits=TaskWorkLogAudit.objects.filter(worklog__task=task).select_related("changed_by", "worklog"),
            comments=task.comments.select_related("author").prefetch_related("attachments"),
            attachments=task.attachments.select_related("uploaded_by"),
            can_manage_attachments=user.can_assign_tasks,
            total_hours=task.total_hours,
            can_log_work=task.can_log_work(user),
            comment_form=CommentForm(),
            review_form=ReviewForm() if awaiting_review else None,
        )
        return ctx


class TaskCreateView(RoleRequiredMixin, TitleMixin, CreateView):
    allowed_roles = MANAGER_ONLY
    model = Task
    form_class = TaskCreateForm
    template_name = "form.html"
    title = "Assign a task"

    def get_initial(self):
        initial = super().get_initial()
        if self.request.GET.get("project", "").isdigit():
            initial["project"] = int(self.request.GET["project"])
        return initial

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        projects = Project.objects.managed_by(self.request.user).filter(is_active=True)
        ctx["project_members"] = {p.pk: list(p.memberships.values_list("user_id", flat=True)) for p in projects}
        ctx["project_depts"] = {p.pk: p.department_id for p in projects}
        ctx["project_techs"] = {p.pk: list(p.technologies.values_list("pk", flat=True)) for p in projects}
        return ctx

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    @transaction.atomic
    def form_valid(self, form):
        created = []
        for emp in form.cleaned_data["assignees"]:
            task = Task.objects.create(
                title=form.cleaned_data["title"], description=form.cleaned_data["description"],
                project=form.cleaned_data["project"], assigned_to=emp, assigned_by=self.request.user,
                priority=form.cleaned_data["priority"], status=form.cleaned_data["status"],
                assign_date=form.cleaned_data["assign_date"], est_date=form.cleaned_data["est_date"],
                end_date=form.cleaned_data["end_date"], remarks=form.cleaned_data["remarks"])
            services.record_task_created(task, self.request.user, form.cleaned_data.get("change_note", ""))
            created.append(task)
        form.save_attachments_to(created, self.request.user)  # every assignee's task gets its own copy
        self.created = created
        names = ", ".join(t.assigned_to.display_name for t in created)
        messages.success(self.request, f"{len(created)} task(s) assigned: {names}.")
        return redirect(self.get_success_url())

    def get_success_url(self):
        project = self.created[0].project if getattr(self, "created", None) else None
        return reverse("project_detail", args=[project.pk]) if project else reverse("task_list")


class TaskUpdateView(LoginRequiredMixin, TitleMixin, UpdateView):
    template_name = "form.html"
    title = "Update task"

    def get_queryset(self):
        if self.request.user.is_admin_role:
            return Task.objects.none()  # admin is view-only on tasks
        return Task.objects.visible_to(self.request.user)

    def get_form_class(self):
        return TaskForm if self.request.user.can_assign_tasks else TaskEmployeeForm

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        if self.request.user.can_assign_tasks:
            kwargs["user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        if self.request.user.can_assign_tasks:
            ctx["existing_attachments"] = list(self.object.attachments.all())
            ctx["existing_kind"] = "task"
        return ctx

    @transaction.atomic
    def form_valid(self, form):
        response = super().form_valid(form)
        services.record_form_changes(self.object, form, self.request.user)
        if hasattr(form, "save_attachments_to"):
            form.save_attachments_to([self.object], self.request.user)
        messages.success(self.request, "Task updated.")
        return response

    def get_success_url(self):
        return reverse("task_detail", args=[self.object.pk])


class TaskArchiveView(RoleRequiredMixin, View):
    allowed_roles = MANAGER_ONLY

    def post(self, request, pk):
        task = get_object_or_404(Task.objects.visible_to(request.user), pk=pk)
        task.is_archived = not task.is_archived
        task.save(update_fields=["is_archived", "updated_at"])
        messages.success(request, f"{task.display_id} {'archived' if task.is_archived else 'restored'}.")
        return redirect("task_list")


class TaskCommentView(LoginRequiredMixin, View):
    def post(self, request, pk):
        task = get_object_or_404(Task.objects.visible_to(request.user), pk=pk)
        form = CommentForm(request.POST, request.FILES)
        if form.is_valid():
            with transaction.atomic():
                comment = TaskComment.objects.create(task=task, author=request.user,
                                                     body=(form.cleaned_data["body"] or "").strip())
                form.save_attachments_to([comment], request.user)
            notifications.comment_added(task, comment)
            messages.success(request, "Comment added.")
        else:
            messages.error(request, " ".join(e for errs in form.errors.values() for e in errs))
        return redirect("task_detail", pk=task.pk)


class TaskReviewView(RoleRequiredMixin, View):
    allowed_roles = MANAGER_ONLY

    def post(self, request, pk):
        task = get_object_or_404(Task.objects.visible_to(request.user), pk=pk)
        if task.status != Task.Status.PENDING_REVIEW:
            messages.error(request, "This task is not waiting for review.")
            return redirect("task_detail", pk=task.pk)
        form = ReviewForm(request.POST)
        if not form.is_valid():
            messages.error(request, " ".join(e for errs in form.errors.values() for e in errs))
            return redirect("task_detail", pk=task.pk)
        approved = form.cleaned_data["decision"] == ReviewForm.APPROVE
        feedback = form.cleaned_data["feedback"].strip()
        with transaction.atomic():
            services.change_status(task, Task.Status.COMPLETED if approved else Task.Status.IN_PROGRESS,
                                   request.user, remarks=feedback)
            if feedback:
                TaskComment.objects.create(task=task, author=request.user, body=feedback,
                                           kind=TaskComment.Kind.FEEDBACK)
        messages.success(request, "Task approved and completed." if approved else "Task sent back for rework.")
        return redirect("task_detail", pk=task.pk)


# ---------------------------------------------------------------- work logs
class WorkLogCreateView(LoginRequiredMixin, TitleMixin, CreateView):
    model = TaskWorkLog
    form_class = WorkLogForm
    template_name = "form.html"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        self.task = get_object_or_404(Task.objects.visible_to(request.user), pk=kwargs["pk"])
        if not self.task.can_log_work(request.user):
            raise PermissionDenied
        self.title = f"Log work on {self.task.display_id}"
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = TaskWorkLog(task=self.task, employee=self.task.assigned_to)
        return kwargs

    @transaction.atomic
    def form_valid(self, form):
        response = super().form_valid(form)
        self.task.technologies.add(*self.object.technologies.all())
        messages.success(self.request, f"Logged {self.object.duration_hours} h.")
        return response

    def get_success_url(self):
        return reverse("task_detail", args=[self.task.pk])


class WorkLogUpdateView(RoleRequiredMixin, TitleMixin, UpdateView):
    """Managers and admins correct work logs. Every changed field is written to the audit trail."""
    allowed_roles = MANAGER_ONLY
    form_class = WorkLogCorrectionForm
    template_name = "form.html"
    title = "Correct work log"

    def get_queryset(self):
        return TaskWorkLog.objects.filter(task__in=Task.objects.visible_to(self.request.user)).select_related("task")

    @staticmethod
    def _fmt(value):
        return f"{value:.2f}" if hasattr(value, "quantize") else ("" if value in (None, False) else str(value))

    @transaction.atomic
    def form_valid(self, form):
        user, reason = self.request.user, form.cleaned_data["reason"]
        old_techs = ", ".join(sorted(t.name for t in self.object.technologies.all()))
        rows = []
        for field in form.changed_data:
            if field == "reason":
                continue
            if field == "technologies":
                old, new = old_techs, ", ".join(sorted(t.name for t in form.cleaned_data["technologies"]))
            else:
                old, new = self._fmt(form.initial.get(field)), self._fmt(form.cleaned_data.get(field))
            if old != new:
                rows.append(TaskWorkLogAudit(worklog=self.object, changed_by=user, field=field,
                                             old_value=old, new_value=new, reason=reason))
        response = super().form_valid(form)
        TaskWorkLogAudit.objects.bulk_create(rows)
        self.object.task.technologies.add(*self.object.technologies.all())
        messages.success(self.request, "Work log corrected.")
        return response

    def get_success_url(self):
        return reverse("task_detail", args=[self.object.task_id])


class WorkLogAttachmentView(LoginRequiredMixin, View):
    """Attachments are never served from a public URL; access follows task visibility."""

    def get(self, request, pk):
        log = get_object_or_404(TaskWorkLog.objects.filter(task__in=Task.objects.visible_to(request.user)), pk=pk)
        if not log.attachment:
            raise Http404
        try:
            handle = log.attachment.open("rb")
        except OSError:  # record exists but the file is gone from the media folder
            messages.error(request, MISSING_FILE_MESSAGE)
            return redirect("task_detail", pk=log.task_id)
        return FileResponse(handle, as_attachment=True, filename=os.path.basename(log.attachment.name))


# ---------------------------------------------------------------- attached documents
def _attachment_queryset(user, kind):
    """Documents the user may open: access follows the project / task they belong to."""
    if kind == "project":
        return ProjectAttachment.objects.filter(project__in=Project.objects.visible_to(user))
    if kind == "task":
        return TaskAttachment.objects.filter(task__in=Task.objects.visible_to(user))
    if kind == "comment":
        return TaskCommentAttachment.objects.filter(comment__task__in=Task.objects.visible_to(user))
    raise Http404


def _can_delete_attachment(user, kind, att):
    if att.uploaded_by_id == user.pk:
        return True
    if not user.can_assign_tasks:
        return False
    if kind == "project":
        return Project.objects.managed_by(user).filter(pk=att.project_id).exists()
    return kind == "task"  # a manager who can see the task may tidy its documents; comments stay with their author


class AttachmentDownloadView(LoginRequiredMixin, View):
    """Documents are never served from a public URL; access follows task / project visibility."""

    def get(self, request, kind, pk):
        att = get_object_or_404(_attachment_queryset(request.user, kind), pk=pk)
        try:
            handle = att.file.open("rb")
        except OSError:
            messages.error(request, MISSING_FILE_MESSAGE)
            return redirect(request.META.get("HTTP_REFERER") or "task_list")
        return FileResponse(handle, as_attachment=True, filename=att.basename)


class AttachmentDeleteView(LoginRequiredMixin, View):
    def post(self, request, kind, pk):
        att = get_object_or_404(_attachment_queryset(request.user, kind), pk=pk)
        if not _can_delete_attachment(request.user, kind, att):
            raise PermissionDenied
        if kind == "project":
            back = redirect("project_detail", pk=att.project_id)
        elif kind == "task":
            back = redirect("task_detail", pk=att.task_id)
        else:
            back = redirect("task_detail", pk=att.comment.task_id)
        name = att.name
        att.file.delete(save=False)
        att.delete()
        messages.success(request, f"Removed {name}.")
        return back


# ---------------------------------------------------------------- technologies (manager only)
class TechnologyListView(RoleRequiredMixin, ListView):
    allowed_roles = MANAGER_ONLY
    template_name = "tasks/technology_list.html"
    context_object_name = "technologies"

    def get_queryset(self):
        return Technology.objects.annotate(task_count=Count("tasks", distinct=True))


class TechnologyCreateView(RoleRequiredMixin, TitleMixin, SuccessMessageMixin, CreateView):
    allowed_roles = MANAGER_ONLY
    form_class = TechnologyForm
    template_name = "form.html"
    title = "Add technology"
    success_url = reverse_lazy("technology_list")
    success_message = "Technology added."


class TechnologyUpdateView(RoleRequiredMixin, TitleMixin, SuccessMessageMixin, UpdateView):
    allowed_roles = MANAGER_ONLY
    model = Technology
    form_class = TechnologyForm
    template_name = "form.html"
    title = "Edit technology"
    success_url = reverse_lazy("technology_list")
    success_message = "Technology updated."
