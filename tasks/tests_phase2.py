import tempfile
from datetime import timedelta
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from projects.models import Department, Project

from . import services
from .models import (Task, TaskComment, TaskDeadlineChange, TaskStatusHistory, TaskWorkLog, TaskWorkLogAudit,
                     Technology)
from .tests import BaseCase

TODAY = timezone.localdate


class Phase2Case(BaseCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.tech = Technology.objects.create(name="Django")
        cls.tech2 = Technology.objects.create(name="MySQL")

    def log_payload(self, **over):
        data = {"work_date": TODAY().isoformat(), "duration_hours": "2.5", "description": "Built the form",
                "remarks": ""}
        data.update(over)
        return data

    def make_log(self, hours="2", desc="Earlier work", **kw):
        return TaskWorkLog.objects.create(task=self.t1, employee=self.emp1, work_date=TODAY(),
                                          duration_hours=Decimal(hours), description=desc, **kw)


class StatusHistoryTests(Phase2Case):
    def test_creation_logs_initial_status(self):
        self.client.force_login(self.manager)
        self.client.post(reverse("task_create"), self.create_payload())
        h = TaskStatusHistory.objects.get(task__title="New")
        self.assertEqual((h.from_status, h.to_status, h.changed_by), ("", "not_started", self.manager))

    def test_employee_status_change_is_logged_with_note(self):
        self.client.force_login(self.emp1)
        self.client.post(reverse("task_update", args=[self.t1.pk]),
                         {"status": "in_progress", "remarks": "", "backup": "", "change_note": "Started"})
        h = self.t1.status_history.get()
        self.assertEqual((h.from_status, h.to_status, h.changed_by, h.remarks),
                         ("not_started", "in_progress", self.emp1, "Started"))

    def test_unchanged_status_is_not_logged(self):
        self.client.force_login(self.emp1)
        self.client.post(reverse("task_update", args=[self.t1.pk]), {"status": "not_started", "remarks": "hi", "backup": ""})
        self.assertEqual(self.t1.status_history.count(), 0)

    def test_change_status_service(self):
        h = services.change_status(self.t1, "blocked", self.manager, "Waiting on client")
        self.assertEqual((h.from_status, h.to_status, h.remarks), ("not_started", "blocked", "Waiting on client"))
        self.assertIsNone(services.change_status(self.t1, "blocked", self.manager))
        self.assertEqual(self.t1.status_history.count(), 1)

    def test_deadline_change_logged_with_reason_only_when_changed(self):
        old = self.t1.est_date
        self.client.force_login(self.manager)
        self.client.post(reverse("task_update", args=[self.t1.pk]),
                         self.payload(est_date=old.isoformat(), change_note="no change"))
        self.assertEqual(TaskDeadlineChange.objects.count(), 0)
        new = old + timedelta(days=7)
        self.client.post(reverse("task_update", args=[self.t1.pk]),
                         self.payload(est_date=new.isoformat(), change_note="Client request"))
        c = TaskDeadlineChange.objects.get()
        self.assertEqual((c.old_date, c.new_date, c.reason, c.changed_by), (old, new, "Client request", self.manager))


class WorkLogTests(Phase2Case):
    def post_log(self, user=None, task=None, **over):
        self.client.force_login(user or self.emp1)
        return self.client.post(reverse("worklog_create", args=[(task or self.t1).pk]), self.log_payload(**over))

    def test_total_hours_is_summed_from_logs(self):
        self.post_log(duration_hours="2.5")
        self.post_log(duration_hours="1.5", description="Second session")
        self.assertEqual(self.t1.work_logs.count(), 2)
        self.assertEqual(self.t1.total_hours, Decimal("4.00"))
        self.assertEqual(Task.objects.with_hours().get(pk=self.t1.pk).logged_hours, Decimal("4.00"))
        self.assertEqual(Task.objects.with_hours().get(pk=self.t2.pk).logged_hours, Decimal("0.00"))

    def test_start_end_time_sets_duration(self):
        self.post_log(duration_hours="", start_time="09:00", end_time="11:30")
        self.assertEqual(self.t1.work_logs.get().duration_hours, Decimal("2.50"))

    def test_invalid_entries_rejected(self):
        bad = [dict(start_time="11:00", end_time="09:00", duration_hours=""),
               dict(start_time="09:00", end_time="", duration_hours=""),
               dict(duration_hours=""), dict(duration_hours="0"), dict(duration_hours="25"), dict(duration_hours="-1"),
               dict(work_date=(TODAY() + timedelta(days=1)).isoformat()),
               dict(work_date=(TODAY() - timedelta(days=3)).isoformat())]  # before assign date
        for over in bad:
            r = self.post_log(**over)
            self.assertEqual(r.status_code, 200, over)
        self.assertEqual(self.t1.work_logs.count(), 0)

    def test_duplicate_submission_rejected(self):
        self.assertEqual(self.post_log().status_code, 302)
        self.assertEqual(self.post_log().status_code, 200)
        self.assertEqual(self.t1.work_logs.count(), 1)

    def test_daily_total_cannot_exceed_24_hours(self):
        self.make_log(hours="20")
        self.assertEqual(self.post_log(duration_hours="5", description="More").status_code, 200)
        self.assertEqual(self.post_log(duration_hours="4", description="Fits").status_code, 302)

    def test_only_assignee_can_log(self):
        self.assertEqual(self.post_log(user=self.emp2).status_code, 404)
        self.assertEqual(self.post_log(user=self.manager).status_code, 403)
        self.assertEqual(self.t1.work_logs.count(), 0)

    def test_cannot_log_on_cancelled_task(self):
        self.t1.status = "cancelled"
        self.t1.save()
        self.assertEqual(self.post_log().status_code, 403)

    def test_log_technologies_propagate_to_task(self):
        self.post_log(technologies=[self.tech.pk])
        self.assertIn(self.tech, self.t1.technologies.all())

    def test_employee_can_set_task_technologies(self):
        self.client.force_login(self.emp1)
        self.client.post(reverse("task_update", args=[self.t1.pk]),
                         {"status": "in_progress", "technologies": [self.tech.pk, self.tech2.pk], "remarks": "", "backup": ""})
        self.assertEqual(set(self.t1.technologies.all()), {self.tech, self.tech2})

    def test_attachment_validation_and_protected_download(self):
        with tempfile.TemporaryDirectory() as tmp, override_settings(MEDIA_ROOT=tmp, MAX_UPLOAD_MB=1):
            self.client.force_login(self.emp1)
            url = reverse("worklog_create", args=[self.t1.pk])
            r = self.client.post(url, self.log_payload(attachment=SimpleUploadedFile("run.exe", b"x")))
            self.assertEqual(r.status_code, 200)
            r = self.client.post(url, self.log_payload(attachment=SimpleUploadedFile("big.txt", b"x" * (1024 * 1024 + 1))))
            self.assertEqual(r.status_code, 200)
            self.assertEqual(self.t1.work_logs.count(), 0)
            r = self.client.post(url, self.log_payload(attachment=SimpleUploadedFile("note.txt", b"hello")))
            self.assertEqual(r.status_code, 302)
            log = self.t1.work_logs.get()
            dl = reverse("worklog_attachment", args=[log.pk])
            resp = self.client.get(dl)
            self.assertEqual(b"".join(resp.streaming_content), b"hello")
            resp.close()
            self.client.force_login(self.emp2)
            self.assertEqual(self.client.get(dl).status_code, 404)
            self.client.logout()
            self.assertEqual(self.client.get(dl).status_code, 302)


class CorrectionTests(Phase2Case):
    def correction(self, log, **over):
        data = {"work_date": TODAY().isoformat(), "duration_hours": "3", "description": log.description,
                "remarks": "", "reason": "Typo in hours"}
        data.update(over)
        return self.client.post(reverse("worklog_update", args=[log.pk]), data)

    def test_manager_correction_writes_audit_and_updates_total(self):
        log = self.make_log(hours="2")
        self.client.force_login(self.manager)
        self.assertEqual(self.correction(log).status_code, 302)
        log.refresh_from_db()
        self.assertEqual(log.duration_hours, Decimal("3.00"))
        a = TaskWorkLogAudit.objects.get()
        self.assertEqual((a.field, a.old_value, a.new_value, a.reason, a.changed_by),
                         ("duration_hours", "2.00", "3.00", "Typo in hours", self.manager))
        self.assertEqual(self.t1.total_hours, Decimal("3.00"))

    def test_reason_is_required(self):
        log = self.make_log(hours="2")
        self.client.force_login(self.manager)
        self.assertEqual(self.correction(log, reason="").status_code, 200)
        log.refresh_from_db()
        self.assertEqual(log.duration_hours, Decimal("2.00"))

    def test_unchanged_correction_writes_no_audit_rows(self):
        log = self.make_log(hours="2")
        self.client.force_login(self.manager)
        self.correction(log, duration_hours="2.0")
        self.assertEqual(TaskWorkLogAudit.objects.count(), 0)

    def test_permissions(self):
        log = self.make_log()
        self.client.force_login(self.emp1)
        self.assertEqual(self.correction(log).status_code, 403)
        self.client.force_login(self.other_manager)
        self.assertEqual(self.correction(log).status_code, 404)


class ReviewAndCommentTests(Phase2Case):
    def review(self, user, **data):
        self.client.force_login(user)
        return self.client.post(reverse("task_review", args=[self.t1.pk]), data)

    def setUp(self):
        self.t1.status = "pending_review"
        self.t1.save()

    def test_approve_completes_task_and_records_history(self):
        self.review(self.manager, decision="approve", feedback="Great work")
        self.t1.refresh_from_db()
        self.assertEqual(self.t1.status, "completed")
        self.assertEqual(self.t1.completed_date, TODAY())
        h = self.t1.status_history.get()
        self.assertEqual((h.from_status, h.to_status, h.changed_by), ("pending_review", "completed", self.manager))
        self.assertEqual(self.t1.comments.get().kind, TaskComment.Kind.FEEDBACK)

    def test_rework_needs_feedback_and_reopens(self):
        self.review(self.manager, decision="rework", feedback="")
        self.t1.refresh_from_db()
        self.assertEqual(self.t1.status, "pending_review")
        self.review(self.manager, decision="rework", feedback="Add tests")
        self.t1.refresh_from_db()
        self.assertEqual(self.t1.status, "in_progress")

    def test_employee_cannot_review(self):
        self.assertEqual(self.review(self.emp1, decision="approve").status_code, 403)
        self.t1.refresh_from_db()
        self.assertEqual(self.t1.status, "pending_review")

    def test_review_only_when_pending(self):
        self.client.force_login(self.manager)
        self.client.post(reverse("task_review", args=[self.t2.pk]), {"decision": "approve"})
        self.t2.refresh_from_db()
        self.assertEqual(self.t2.status, "not_started")

    def test_comments_follow_task_visibility(self):
        self.client.force_login(self.emp1)
        self.client.post(reverse("task_comment", args=[self.t1.pk]), {"body": "Question"})
        self.assertEqual(self.t1.comments.count(), 1)
        self.assertEqual(self.client.post(reverse("task_comment", args=[self.t2.pk]), {"body": "x"}).status_code, 404)
        self.assertEqual(self.t2.comments.count(), 0)


class TechnologyAndPageTests(Phase2Case):
    def test_only_manager_manages_technologies(self):
        self.client.force_login(self.emp1)
        self.assertEqual(self.client.get(reverse("technology_list")).status_code, 403)
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(reverse("technology_list")).status_code, 200)
        self.client.post(reverse("technology_create"), {"name": "React", "is_active": "on"})
        self.assertTrue(Technology.objects.filter(name="React").exists())
        self.client.force_login(self.admin)
        for name in ("technology_list", "technology_create"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)
        self.client.post(reverse("technology_create"), {"name": "Vue", "is_active": "on"})
        self.assertFalse(Technology.objects.filter(name="Vue").exists())
        self.assertNotContains(self.client.get(reverse("dashboard")), reverse("technology_list"))

    def test_pages_render_with_data(self):
        log = self.make_log()
        log.technologies.add(self.tech)
        services.change_status(self.t1, "pending_review", self.emp1, "done")
        urls = [reverse("task_detail", args=[self.t1.pk]), reverse("task_list"), reverse("dashboard"),
                reverse("project_detail", args=[self.project.pk])]
        for user in (self.admin, self.manager, self.emp1):
            self.client.force_login(user)
            for url in urls:
                self.assertEqual(self.client.get(url).status_code, 200, f"{user.role} {url}")
        self.client.force_login(self.emp1)
        self.assertEqual(self.client.get(reverse("worklog_create", args=[self.t1.pk])).status_code, 200)
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(reverse("worklog_update", args=[log.pk])).status_code, 200)


class ProjectTechAndDaysTests(Phase2Case):
    def test_manager_sets_project_technologies_and_est_date(self):
        self.client.force_login(self.manager)
        r = self.client.post(reverse("project_create"), {
            "department": self.dept.pk, "name": "Gamma", "code": "GAM", "is_active": "on", "technologies": [self.tech.pk],
            "team_members": [self.emp1.pk, self.emp2.pk],
            "start_date": "2026-10-01", "est_date": "2026-11-01"})
        self.assertEqual(r.status_code, 302)
        proj = self.project.__class__.objects.get(code="GAM")
        self.assertEqual(list(proj.technologies.all()), [self.tech])
        self.assertEqual(str(proj.est_date), "2026-11-01")
        # Daily task form has no technologies field and accepts an end date.
        r = self.client.post(reverse("task_create"), self.create_payload(
            project=proj.pk, assignees=[self.emp1.pk, self.emp2.pk], end_date=str(TODAY())))
        self.assertEqual(r.status_code, 302)
        tasks = Task.objects.filter(project=proj)
        self.assertEqual(tasks.count(), 2)
        self.assertTrue(all(t.end_date == TODAY() for t in tasks))
        self.assertTrue(all(t.technologies.count() == 0 for t in tasks))

    def test_days_taken_and_labels(self):
        t = self.t1
        t.assign_date = TODAY() - timedelta(days=3)
        t.status = "completed"
        t.save()
        self.assertEqual(t.days_taken, 3)  # assigned 3 days ago, completed today
        t.completed_date = t.assign_date + timedelta(days=2)
        self.assertEqual(t.days_taken, 2)
        self.assertEqual(t.duration_label, "2 days")
        t.completed_date = t.assign_date
        self.assertEqual(t.duration_label, "Same day")
        open_task = self.t2
        open_task.assign_date = TODAY() - timedelta(days=2)
        self.assertEqual(open_task.duration_label, "Day 3")

    def test_project_page_shows_days_per_employee(self):
        self.t1.assign_date = TODAY() - timedelta(days=4)
        self.t1.status = "completed"
        self.t1.save()
        Task.objects.filter(pk=self.t1.pk).update(completed_date=TODAY() - timedelta(days=1))
        self.client.force_login(self.manager)
        r = self.client.get(reverse("project_detail", args=[self.project.pk]))
        self.assertEqual(r.status_code, 200)
        stats = {row["name"]: row for row in r.context["team_stats"]}
        self.assertEqual(stats["emp1"]["avg_days"], 3.0)
        self.assertEqual(r.context["avg_days"], 3.0)
        self.client.force_login(self.emp1)
        self.assertNotContains(self.client.get(reverse("project_detail", args=[self.project.pk])), "Days taken by employee")

    def test_sidebar_and_dashboard_project_cards(self):
        self.client.force_login(self.manager)
        r = self.client.get(reverse("dashboard"))
        self.assertContains(r, 'class="sidebar')
        self.assertContains(r, "Assign daily task")
        self.assertEqual([p.pk for p in r.context["project_cards"]], [self.project.pk])
        self.client.logout()
        self.assertEqual(self.client.get("/accounts/login/").status_code, 200)
