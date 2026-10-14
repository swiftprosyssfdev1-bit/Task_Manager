from datetime import date, timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from projects.models import Department, Project, ProjectMembership
from tasks.models import (Task, TaskAssignmentHistory, TaskDeadlineChange, TaskStatusHistory, TaskWorkLog)

from . import services
from .models import Holiday

MON = date(2026, 10, 5)  # a Monday


def _cards(response):
    return {c[0]: c[1] for c in response.context["cards"]}


class BusinessDayTests(SimpleTestCase):
    def test_same_day_is_zero(self):
        self.assertEqual(services.business_days(MON, MON), 0)

    def test_monday_to_friday_is_four(self):
        self.assertEqual(services.business_days(MON, MON + timedelta(days=4)), 4)

    def test_friday_to_monday_is_one(self):
        self.assertEqual(services.business_days(date(2026, 10, 9), date(2026, 10, 12)), 1)

    def test_weekend_start_counts_only_working_days_after(self):
        self.assertEqual(services.business_days(date(2026, 10, 10), date(2026, 10, 12)), 1)

    def test_weekend_only_span_is_zero(self):
        self.assertEqual(services.business_days(date(2026, 10, 10), date(2026, 10, 11)), 0)

    def test_holidays_are_skipped(self):
        self.assertEqual(services.business_days(MON, MON + timedelta(days=4), {date(2026, 10, 7)}), 3)

    def test_end_before_start_is_zero(self):
        self.assertEqual(services.business_days(MON, MON - timedelta(days=3)), 0)
        self.assertEqual(services.calendar_days(MON, MON - timedelta(days=3)), 0)


class DurationTests(SimpleTestCase):
    def test_early(self):
        d = services.durations(MON, MON + timedelta(days=5), MON + timedelta(days=3))
        self.assertEqual((d.actual_calendar, d.estimated, d.days_early, d.delay_days, d.variance, d.on_time),
                         (3, 5, 2, 0, -2, True))

    def test_late(self):
        d = services.durations(MON, MON + timedelta(days=2), MON + timedelta(days=6))
        self.assertEqual((d.actual_calendar, d.estimated, d.days_early, d.delay_days, d.variance, d.on_time),
                         (6, 2, 0, 4, 4, False))

    def test_completed_on_est_date_is_on_time(self):
        d = services.durations(MON, MON + timedelta(days=3), MON + timedelta(days=3))
        self.assertTrue(d.on_time)
        self.assertEqual((d.delay_days, d.days_early, d.variance), (0, 0, 0))

    def test_same_day_completion(self):
        d = services.durations(MON, MON, MON)
        self.assertEqual((d.actual_calendar, d.actual_business, d.estimated, d.on_time), (0, 0, 0, True))

    def test_no_estimate_is_not_late_or_early(self):
        d = services.durations(MON, None, MON + timedelta(days=4))
        self.assertEqual(d.actual_calendar, 4)
        self.assertIsNone(d.estimated)
        self.assertIsNone(d.on_time)
        self.assertIsNone(d.delay_days)
        self.assertIsNone(d.days_early)
        self.assertIsNone(d.variance)

    def test_business_days_exclude_weekend_but_calendar_does_not(self):
        d = services.durations(date(2026, 10, 9), None, date(2026, 10, 12))
        self.assertEqual((d.actual_calendar, d.actual_business), (3, 1))


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        R = User.Role
        cls.admin = User.objects.create_user("admin1", password="x", role=R.ADMIN)
        cls.dept = Department.objects.create(name="Engineering")
        cls.other_dept = Department.objects.create(name="Sales")
        cls.manager = User.objects.create_user("mgr", password="x", role=R.MANAGER, department=cls.dept)
        cls.other_manager = User.objects.create_user("mgr2", password="x", role=R.MANAGER, department=cls.other_dept)
        cls.emp1 = User.objects.create_user("emp1", password="x", role=R.EMPLOYEE, first_name="Asha", department=cls.dept, manager=cls.manager)
        cls.emp2 = User.objects.create_user("emp2", password="x", role=R.EMPLOYEE, first_name="Ravi", department=cls.dept, manager=cls.manager)
        cls.project = Project.objects.create(name="Alpha", code="ALP", manager=cls.manager, department=cls.dept)
        cls.other_project = Project.objects.create(name="Beta", code="BET", manager=cls.other_manager,
                                                   department=cls.other_dept)
        for e in (cls.emp1, cls.emp2):
            ProjectMembership.objects.create(project=cls.project, user=e)
        cls.today = timezone.localdate()
        cls.start = cls.today.replace(day=1)

    def make(self, emp=None, assign=None, est=None, done=None, status=None, project=None, **kw):
        assign = assign or self.today - timedelta(days=10)
        status = status or (Task.Status.COMPLETED if done else Task.Status.NOT_STARTED)
        t = Task.objects.create(title="t", description="d", project=project or self.project,
                                assigned_to=emp or self.emp1, assigned_by=self.manager, assign_date=assign,
                                est_date=est, status=status, completed_date=done, **kw)
        return t

    def stats(self, qs=None, start=None, end=None, **kw):
        qs = qs if qs is not None else Task.objects.all()
        return {r["user"].pk: r for r in services.employee_stats(
            qs, start or self.today - timedelta(days=60), end or self.today, **kw)}


class EmployeeStatsTests(Base):
    def test_on_time_late_and_no_estimate(self):
        a = self.today - timedelta(days=10)
        self.make(assign=a, est=a + timedelta(days=5), done=a + timedelta(days=3))   # early
        self.make(assign=a, est=a + timedelta(days=2), done=a + timedelta(days=6))   # late
        self.make(assign=a, est=None, done=a + timedelta(days=4))                    # no estimate
        r = self.stats()[self.emp1.pk]
        self.assertEqual((r["completed"], r["on_time"], r["late"], r["no_estimate"]), (3, 1, 1, 1))
        self.assertEqual(r["on_time_pct"], 50.0)          # the no-estimate task is excluded
        self.assertEqual(r["avg_calendar"], round((3 + 6 + 4) / 3, 1))

    def test_on_time_pct_is_none_without_any_estimates(self):
        a = self.today - timedelta(days=5)
        self.make(assign=a, est=None, done=a + timedelta(days=1))
        self.assertIsNone(self.stats()[self.emp1.pk]["on_time_pct"])

    def test_completion_rate_ignores_cancelled(self):
        a = self.today - timedelta(days=5)
        self.make(assign=a, est=a + timedelta(days=3), done=a + timedelta(days=2))
        self.make(assign=a, status=Task.Status.IN_PROGRESS)
        self.make(assign=a, status=Task.Status.CANCELLED)
        r = self.stats()[self.emp1.pk]
        self.assertEqual((r["assigned"], r["cohort"], r["cohort_done"]), (3, 2, 1))
        self.assertEqual(r["completion_rate"], 50.0)

    def test_overdue_blocked_and_pending_counts(self):
        a = self.today - timedelta(days=10)
        self.make(assign=a, est=self.today - timedelta(days=1), status=Task.Status.IN_PROGRESS)
        self.make(assign=a, est=self.today + timedelta(days=2), status=Task.Status.BLOCKED)
        self.make(assign=a, est=self.today - timedelta(days=3), status=Task.Status.COMPLETED,
                  done=self.today - timedelta(days=4))
        r = self.stats()[self.emp1.pk]
        self.assertEqual((r["open_now"], r["overdue_now"], r["blocked_now"], r["in_progress_now"], r["pending_now"]),
                         (2, 1, 1, 1, 1))

    def test_hours_come_from_work_logs_and_period(self):
        t = self.make(status=Task.Status.IN_PROGRESS)
        for days_ago, hours in ((1, "2.50"), (2, "1.50"), (40, "6.00")):
            TaskWorkLog.objects.create(task=t, employee=self.emp1, work_date=self.today - timedelta(days=days_ago),
                                       duration_hours=Decimal(hours), description="x")
        r = self.stats(start=self.today - timedelta(days=7))[self.emp1.pk]
        self.assertEqual(r["logged_hours"], Decimal("4.00"))

    def test_average_hours_per_completed_task(self):
        a = self.today - timedelta(days=5)
        t1 = self.make(assign=a, est=a + timedelta(days=3), done=a + timedelta(days=2))
        t2 = self.make(assign=a, est=a + timedelta(days=3), done=a + timedelta(days=2))
        for t, h in ((t1, "2.00"), (t2, "5.00")):
            TaskWorkLog.objects.create(task=t, employee=self.emp1, work_date=a, duration_hours=Decimal(h), description="x")
        self.assertEqual(self.stats()[self.emp1.pk]["avg_hours"], 3.5)

    def test_period_filters_completed_and_assigned(self):
        old = self.today - timedelta(days=90)
        self.make(assign=old, est=old + timedelta(days=3), done=old + timedelta(days=2))
        r = self.stats(start=self.today - timedelta(days=30)).get(self.emp1.pk)
        self.assertIsNone(r)

    def test_business_days_average_uses_holidays(self):
        Holiday.objects.create(date=date(2026, 10, 7), name="Break")
        self.make(assign=MON, est=MON + timedelta(days=4), done=MON + timedelta(days=4))
        r = self.stats(start=MON, end=MON + timedelta(days=10), holidays=services.get_holidays())[self.emp1.pk]
        self.assertEqual((r["avg_calendar"], r["avg_business"]), (4.0, 3.0))

    def test_revised_deadline_is_judged_against_original_too(self):
        a = self.today - timedelta(days=12)
        t = self.make(assign=a, est=a + timedelta(days=10), done=a + timedelta(days=8))
        TaskDeadlineChange.objects.create(task=t, old_date=a + timedelta(days=5), new_date=a + timedelta(days=10),
                                          changed_by=self.manager, reason="scope")
        r = self.stats()[self.emp1.pk]
        self.assertEqual(r["on_time_pct"], 100.0)       # against the approved date
        self.assertEqual(r["on_time_orig_pct"], 0.0)    # against the first date
        m = services.completed_metrics(Task.objects.all())[0]
        self.assertTrue(m.deadline_revised)
        self.assertFalse(m.on_time_vs_original)

    def test_reassigned_and_blocked_flags(self):
        a = self.today - timedelta(days=6)
        t = self.make(assign=a, est=a + timedelta(days=5), done=a + timedelta(days=4))
        TaskAssignmentHistory.objects.create(task=t, from_employee=self.emp2, to_employee=self.emp1,
                                             changed_by=self.manager)
        TaskStatusHistory.objects.create(task=t, from_status="in_progress", to_status="blocked",
                                         changed_by=self.emp1)
        m = services.completed_metrics(Task.objects.all())[0]
        self.assertTrue(m.reassigned)
        self.assertTrue(m.was_blocked)

    def test_high_workload_flag(self):
        for _ in range(services.workload_threshold()):
            self.make(status=Task.Status.IN_PROGRESS)
        self.assertTrue(self.stats()[self.emp1.pk]["high_workload"])

    def test_ensure_users_returns_empty_row(self):
        rows = services.employee_stats(Task.objects.none(), self.start, self.today, ensure_users=[self.emp2])
        self.assertEqual((rows[0]["assigned"], rows[0]["completion_rate"]), (0, None))


class ProjectStatsTests(Base):
    def test_counts_and_completion(self):
        a = self.today - timedelta(days=8)
        self.make(assign=a, est=a + timedelta(days=4), done=a + timedelta(days=6))
        self.make(assign=a, status=Task.Status.BLOCKED)
        self.make(assign=a, status=Task.Status.CANCELLED)
        p = [r for r in services.project_stats(Task.objects.all()) if r["id"] == self.project.pk][0]
        self.assertEqual((p["total"], p["completed"], p["open"], p["blocked"], p["cancelled"]), (3, 1, 1, 1, 1))
        self.assertEqual(p["completion_pct"], 50.0)
        self.assertEqual((p["avg_estimated"], p["avg_actual"]), (4.0, 6.0))


class TrendTests(Base):
    def test_weekly_and_monthly_buckets(self):
        this_monday = self.today - timedelta(days=self.today.weekday())
        a = self.today - timedelta(days=30)
        self.make(assign=a, done=this_monday)
        self.make(assign=a, done=this_monday - timedelta(days=1))   # last week
        self.make(assign=a, done=this_monday - timedelta(weeks=20))  # outside the window
        t = services.completion_trend(Task.objects.all(), self.today)
        self.assertEqual(len(t["weekly"]["data"]), 8)
        self.assertEqual(t["weekly"]["data"][-1], 1)
        self.assertEqual(t["weekly"]["data"][-2], 1)
        self.assertEqual(len(t["monthly"]["data"]), 6)
        self.assertLessEqual(sum(t["weekly"]["data"]), 2)


class PermissionTests(Base):
    def setUp(self):
        a = self.today - timedelta(days=5)
        self.make(assign=a, est=a + timedelta(days=3), done=a + timedelta(days=2))
        self.make(emp=self.emp2, assign=a, est=a + timedelta(days=3), done=a + timedelta(days=2))

    def test_anonymous_redirected(self):
        self.assertEqual(self.client.get(reverse("team_analytics")).status_code, 302)
        self.assertEqual(self.client.get(reverse("employee_report", args=[self.emp1.pk])).status_code, 302)

    def test_employee_cannot_open_team_analytics(self):
        self.client.force_login(self.emp1)
        self.assertEqual(self.client.get(reverse("team_analytics")).status_code, 403)

    def test_employee_sees_own_report_only(self):
        self.client.force_login(self.emp1)
        self.assertEqual(self.client.get(reverse("employee_report", args=[self.emp1.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("employee_report", args=[self.emp2.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("employee_report", args=[self.manager.pk])).status_code, 404)

    def test_manager_sees_employees_in_scope_only(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(reverse("employee_report", args=[self.emp1.pk])).status_code, 200)
        self.client.force_login(self.other_manager)
        self.assertEqual(self.client.get(reverse("employee_report", args=[self.emp1.pk])).status_code, 404)

    def test_manager_team_page_lists_only_scoped_employees(self):
        self.client.force_login(self.other_manager)
        r = self.client.get(reverse("team_analytics"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["employees"], [])
        self.assertEqual(r.context["projects"], [])

    def test_admin_sees_everything(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse("team_analytics"), {"start": (self.today - timedelta(days=30)).isoformat()})
        self.assertEqual({row["user"].pk for row in r.context["employees"]}, {self.emp1.pk, self.emp2.pk})
        self.assertEqual(self.client.get(reverse("employee_report", args=[self.emp2.pk])).status_code, 200)

    def test_report_for_non_employee_is_404(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("employee_report", args=[self.manager.pk])).status_code, 404)

    def test_bad_date_range_falls_back_without_error(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse("team_analytics"), {"start": "2026-10-10", "end": "2026-10-01"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["start"], self.start)

    def test_report_page_renders_for_employee_with_no_tasks(self):
        quiet = User.objects.create_user("quiet", password="x", role=User.Role.EMPLOYEE)
        self.client.force_login(quiet)
        r = self.client.get(reverse("employee_report", args=[quiet.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["stats"]["assigned"], 0)


class DashboardTests(Base):
    def setUp(self):
        a = self.today - timedelta(days=5)
        self.make(assign=a, est=a + timedelta(days=3), done=a + timedelta(days=2))
        self.make(emp=self.emp2, assign=a, est=self.today - timedelta(days=1), status=Task.Status.IN_PROGRESS)
        self.make(emp=self.emp2, assign=a, status=Task.Status.PENDING_REVIEW)

    def test_manager_dashboard_has_workload_and_review_list(self):
        self.client.force_login(self.manager)
        r = self.client.get(reverse("dashboard"))
        self.assertEqual(r.status_code, 200)
        self.assertIsNotNone(r.context["charts"]["workload"])
        cards = _cards(r)
        self.assertEqual(cards["Awaiting review"], 1)
        self.assertEqual(cards["Overdue"], 1)
        self.assertContains(r, 'id="chart-data"')

    def test_dashboard_no_longer_draws_status_estimate_or_trend_charts(self):
        for user in (self.manager, self.emp1):
            self.client.force_login(user)
            r = self.client.get(reverse("dashboard"))
            for canvas in ("chStatus", "chEstimate", "chTrend"):
                self.assertNotContains(r, f'id="{canvas}"')
            for title in ("Tasks by status", "Estimated vs actual days", '>Completed tasks</span>'):
                self.assertNotContains(r, title)
            self.assertIn("Today", r.content.decode())

    def test_employee_dashboard_is_limited_to_own_tasks(self):
        self.client.force_login(self.emp1)
        r = self.client.get(reverse("dashboard"))
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.context["charts"]["workload"])
        cards = _cards(r)
        self.assertEqual(cards["My tasks"], 1)
        self.assertEqual(cards["Overdue"], 0)
        self.assertNotContains(r, "emp2")

    def test_employee_hours_card_sums_logs(self):
        t = Task.objects.filter(assigned_to=self.emp1).first()
        for h in ("1.25", "2.00"):
            TaskWorkLog.objects.create(task=t, employee=self.emp1, work_date=self.today - timedelta(days=1),
                                       duration_hours=Decimal(h), description="x")
        self.client.force_login(self.emp1)
        self.assertEqual(_cards(self.client.get(reverse("dashboard")))["Hours logged"], "3.25")

    def test_dashboard_renders_with_no_data(self):
        Task.objects.all().delete()
        for u in (self.admin, self.manager, self.emp1):
            self.client.force_login(u)
            self.assertEqual(self.client.get(reverse("dashboard")).status_code, 200)

    def test_archived_tasks_are_left_out(self):
        Task.objects.filter(assigned_to=self.emp1).update(is_archived=True)
        self.client.force_login(self.emp1)
        self.assertEqual(_cards(self.client.get(reverse("dashboard")))["My tasks"], 0)


class HolidayModelTests(TestCase):
    def test_date_is_unique(self):
        Holiday.objects.create(date=MON, name="A")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Holiday.objects.create(date=MON, name="B")
