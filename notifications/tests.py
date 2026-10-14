from datetime import timedelta

from django.core.cache import cache
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from tasks.models import Task
from tasks.tests import BaseCase

from .models import Notification
from .services import notify_overdue, sweep_overdue_if_due

K = Notification.Kind


class NotificationCase(BaseCase):
    def setUp(self):
        cache.clear()
        Notification.objects.all().delete()  # drop anything created by fixtures

    def mine(self, user, kind=None):
        qs = Notification.objects.filter(recipient=user)
        return qs.filter(kind=kind) if kind else qs


class AssignmentNotificationTests(NotificationCase):
    def test_employee_is_told_when_a_task_is_assigned(self):
        self.client.force_login(self.manager)
        self.client.post(reverse("task_create"), self.create_payload(assignees=[self.emp1.pk, self.emp2.pk]))
        for emp in (self.emp1, self.emp2):
            n = self.mine(emp, K.ASSIGNED).get()
            self.assertIn("assigned you", n.message)
            self.assertEqual(n.task.assigned_to, emp)
        self.assertEqual(self.mine(self.manager).count(), 0)  # nobody is told about their own action

    def test_reassigning_tells_both_employees(self):
        self.client.force_login(self.manager)
        self.client.post(reverse("task_update", args=[self.t1.pk]), self.payload(
            title="T1", assigned_to=self.emp2.pk, status="not_started"))
        self.assertEqual(self.mine(self.emp2, K.ASSIGNED).count(), 1)
        self.assertEqual(self.mine(self.emp1, K.REASSIGNED).count(), 1)

    def test_deadline_change_tells_employee(self):
        self.client.force_login(self.manager)
        new = (timezone.localdate() + timedelta(days=20)).isoformat()
        self.client.post(reverse("task_update", args=[self.t1.pk]), self.payload(
            title="T1", est_date=new, change_note="client moved it"))
        self.assertEqual(self.mine(self.emp1, K.DEADLINE).count(), 1)
        self.assertEqual(self.mine(self.emp2, K.DEADLINE).count(), 0)

    def test_unchanged_save_sends_nothing(self):
        self.client.force_login(self.manager)
        self.client.post(reverse("task_update", args=[self.t1.pk]), self.payload(
            title="T1", est_date=self.t1.est_date.isoformat(), assign_date=self.t1.assign_date.isoformat()))
        self.assertEqual(Notification.objects.count(), 0)


class ReviewNotificationTests(NotificationCase):
    def test_submitting_for_review_tells_the_manager(self):
        self.client.force_login(self.emp1)
        self.client.post(reverse("task_update", args=[self.t1.pk]),
                         {"status": "pending_review", "remarks": "", "backup": ""})
        n = self.mine(self.manager, K.SUBMITTED).get()
        self.assertEqual(n.task, self.t1)
        self.assertEqual(self.mine(self.other_manager).count(), 0)
        self.assertEqual(self.mine(self.emp1).count(), 0)

    def test_blocked_tells_the_manager(self):
        self.client.force_login(self.emp1)
        self.client.post(reverse("task_update", args=[self.t1.pk]), {"status": "blocked", "remarks": "", "backup": ""})
        self.assertEqual(self.mine(self.manager, K.BLOCKED).count(), 1)

    def test_ordinary_progress_does_not_ping_the_manager(self):
        self.client.force_login(self.emp1)
        self.client.post(reverse("task_update", args=[self.t1.pk]),
                         {"status": "in_progress", "remarks": "", "backup": ""})
        self.assertEqual(Notification.objects.count(), 0)

    def test_approval_and_rework_tell_the_employee(self):
        Task.objects.filter(pk=self.t1.pk).update(status="pending_review")
        self.client.force_login(self.manager)
        self.client.post(reverse("task_review", args=[self.t1.pk]), {"decision": "approve", "feedback": "Nice"})
        n = self.mine(self.emp1, K.APPROVED).get()
        self.assertIn("Nice", n.message)
        Task.objects.filter(pk=self.t2.pk).update(status="pending_review")
        self.client.post(reverse("task_review", args=[self.t2.pk]), {"decision": "rework", "feedback": "Fix tests"})
        self.assertIn("Fix tests", self.mine(self.emp2, K.REWORK).get().message)

    def test_feedback_does_not_also_create_a_comment_notification(self):
        Task.objects.filter(pk=self.t1.pk).update(status="pending_review")
        self.client.force_login(self.manager)
        self.client.post(reverse("task_review", args=[self.t1.pk]), {"decision": "approve", "feedback": "Nice"})
        self.assertEqual(self.mine(self.emp1, K.COMMENT).count(), 0)

    def test_comments_notify_the_other_side_only(self):
        self.client.force_login(self.emp1)
        self.client.post(reverse("task_comment", args=[self.t1.pk]), {"body": "Is the spec final?"})
        self.assertEqual(self.mine(self.manager, K.COMMENT).count(), 1)
        self.assertEqual(self.mine(self.emp1).count(), 0)
        self.client.force_login(self.manager)
        self.client.post(reverse("task_comment", args=[self.t1.pk]), {"body": "Yes"})
        self.assertEqual(self.mine(self.emp1, K.COMMENT).count(), 1)
        self.assertEqual(self.mine(self.emp2).count(), 0)

    def test_deactivated_users_are_not_notified(self):
        User.objects.filter(pk=self.manager.pk).update(is_active=False)
        self.client.force_login(self.emp1)
        self.client.post(reverse("task_update", args=[self.t1.pk]),
                         {"status": "pending_review", "remarks": "", "backup": ""})
        self.assertEqual(Notification.objects.count(), 0)


class OverdueNotificationTests(NotificationCase):
    def make_overdue(self, task=None, status="in_progress"):
        task = task or self.t1
        past = timezone.localdate() - timedelta(days=3)
        Task.objects.filter(pk=task.pk).update(assign_date=past - timedelta(days=2), est_date=past, status=status)
        return task

    def test_employee_and_manager_are_told_once(self):
        self.make_overdue()
        self.assertEqual(notify_overdue(), 2)
        self.assertEqual(self.mine(self.emp1, K.OVERDUE).count(), 1)
        self.assertEqual(self.mine(self.manager, K.OVERDUE).count(), 1)
        self.assertEqual(notify_overdue(), 0)  # re-running does not repeat it
        self.assertEqual(Notification.objects.count(), 2)

    def test_not_overdue_or_closed_or_archived_tasks_are_ignored(self):
        self.assertEqual(notify_overdue(), 0)  # t1 and t2 are due in the future
        self.make_overdue(status="completed")
        self.make_overdue(self.t2)
        Task.objects.filter(pk=self.t2.pk).update(is_archived=True)
        self.assertEqual(notify_overdue(), 0)

    def test_pending_review_overdue_only_pings_the_manager(self):
        self.make_overdue(status="pending_review")
        notify_overdue()
        self.assertEqual(self.mine(self.emp1).count(), 0)
        self.assertEqual(self.mine(self.manager, K.OVERDUE).count(), 1)

    def test_new_deadline_can_trigger_a_new_alert(self):
        self.make_overdue()
        notify_overdue()
        Task.objects.filter(pk=self.t1.pk).update(est_date=timezone.localdate() - timedelta(days=1))
        self.assertEqual(notify_overdue(), 2)

    def test_admin_never_gets_notifications(self):
        self.make_overdue()
        notify_overdue()
        self.assertEqual(self.mine(self.admin).count(), 0)

    def test_dashboard_sweeps_lazily_but_only_once_per_interval(self):
        self.make_overdue()
        self.client.force_login(self.emp1)
        self.client.get(reverse("dashboard"))
        self.assertEqual(self.mine(self.emp1, K.OVERDUE).count(), 1)
        self.assertEqual(sweep_overdue_if_due(), 0)  # throttled

    def test_management_command(self):
        self.make_overdue()
        call_command("notify_overdue", stdout=__import__("io").StringIO())
        self.assertEqual(Notification.objects.filter(kind=K.OVERDUE).count(), 2)


class NotificationPageTests(NotificationCase):
    def setUp(self):
        super().setUp()
        self.n1 = Notification.objects.create(recipient=self.emp1, kind=K.ASSIGNED, message="one", task=self.t1)
        self.n2 = Notification.objects.create(recipient=self.emp1, kind=K.COMMENT, message="two", task=self.t1)
        self.theirs = Notification.objects.create(recipient=self.emp2, kind=K.ASSIGNED, message="x", task=self.t2)

    def test_list_shows_only_my_notifications_and_badge(self):
        self.client.force_login(self.emp1)
        r = self.client.get(reverse("notification_list"))
        self.assertEqual({n.pk for n in r.context["notifications"]}, {self.n1.pk, self.n2.pk})
        self.assertEqual(r.context["unread_notifications"], 2)
        self.assertContains(self.client.get(reverse("dashboard")), "text-bg-danger ms-auto")

    def test_unread_filter(self):
        Notification.objects.filter(pk=self.n1.pk).update(is_read=True)
        self.client.force_login(self.emp1)
        r = self.client.get(reverse("notification_list"), {"filter": "unread"})
        self.assertEqual([n.pk for n in r.context["notifications"]], [self.n2.pk])

    def test_opening_marks_read_and_goes_to_task(self):
        self.client.force_login(self.emp1)
        r = self.client.get(reverse("notification_open", args=[self.n1.pk]))
        self.assertRedirects(r, reverse("task_detail", args=[self.t1.pk]))
        self.n1.refresh_from_db()
        self.assertTrue(self.n1.is_read)
        self.assertIsNotNone(self.n1.read_at)

    def test_cannot_open_someone_elses_notification(self):
        self.client.force_login(self.emp1)
        self.assertEqual(self.client.get(reverse("notification_open", args=[self.theirs.pk])).status_code, 404)
        self.theirs.refresh_from_db()
        self.assertFalse(self.theirs.is_read)

    def test_mark_all_read_touches_only_my_rows(self):
        self.client.force_login(self.emp1)
        self.client.post(reverse("notification_read_all"))
        self.assertEqual(self.mine(self.emp1).filter(is_read=False).count(), 0)
        self.theirs.refresh_from_db()
        self.assertFalse(self.theirs.is_read)

    def test_admin_has_no_notifications_page_or_sidebar_link(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("notification_list")).status_code, 403)
        self.assertNotContains(self.client.get(reverse("dashboard")), reverse("notification_list"))

    def test_login_required(self):
        r = self.client.get(reverse("notification_list"))
        self.assertEqual(r.status_code, 302)
        self.assertIn("login", r["Location"])
