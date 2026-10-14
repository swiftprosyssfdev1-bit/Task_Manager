import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse

from projects.models import ProjectAttachment

from .models import Task, TaskAttachment, TaskComment, TaskCommentAttachment
from .tests import BaseCase

TMP = tempfile.mkdtemp()


def up(name, data=b"data"):
    return SimpleUploadedFile(name, data)


@override_settings(MEDIA_ROOT=TMP)
class AttachmentTests(BaseCase):
    # ---- task assignment
    def test_assign_task_with_documents_copies_to_each_assignee(self):
        self.client.force_login(self.manager)
        data = self.create_payload(assignees=[self.emp1.pk, self.emp2.pk])
        data["attachments"] = [up("plan.pdf"), up("sheet.xlsx"), up("pic.PNG"), up("bundle.zip")]
        r = self.client.post(reverse("task_create"), data)
        self.assertEqual(r.status_code, 302)
        tasks = Task.objects.filter(title="New")
        self.assertEqual(tasks.count(), 2)
        for t in tasks:
            self.assertEqual(sorted(a.name for a in t.attachments.all()), ["bundle.zip", "pic.PNG", "plan.pdf", "sheet.xlsx"])
        # separate stored copies: removing one task's document does not break the other's
        files = {a.file.name for a in TaskAttachment.objects.all()}
        self.assertEqual(len(files), 8)

    def test_folder_upload_keeps_relative_paths(self):
        self.client.force_login(self.manager)
        data = self.create_payload()
        data["attachments"] = [up("a.pdf"), up("b.xlsx")]
        data["attachment_paths"] = ["Specs/v2/a.pdf", "Specs/b.xlsx"]
        self.client.post(reverse("task_create"), data)
        names = sorted(TaskAttachment.objects.values_list("name", flat=True))
        self.assertEqual(names, ["Specs/b.xlsx", "Specs/v2/a.pdf"])

    def test_path_traversal_is_neutralised(self):
        self.client.force_login(self.manager)
        data = self.create_payload()
        data["attachments"] = [up("a.pdf")]
        data["attachment_paths"] = ["../../etc/../x/a.pdf"]
        self.client.post(reverse("task_create"), data)
        self.assertEqual(TaskAttachment.objects.get().name, "etc/x/a.pdf")

    def test_disallowed_type_rejects_whole_form(self):
        self.client.force_login(self.manager)
        data = self.create_payload()
        data["attachments"] = [up("ok.pdf"), up("run.exe")]
        r = self.client.post(reverse("task_create"), data)
        self.assertEqual(r.status_code, 200)
        self.assertIn("run.exe: type not allowed", str(r.context["form"].errors["attachments"]))
        self.assertFalse(Task.objects.filter(title="New").exists())

    @override_settings(ATTACHMENT_MAX_MB=1)
    def test_oversize_file_rejected(self):
        self.client.force_login(self.manager)
        data = self.create_payload()
        data["attachments"] = [up("big.pdf", b"x" * (1024 * 1024 + 1))]
        r = self.client.post(reverse("task_create"), data)
        self.assertEqual(r.status_code, 200)
        self.assertIn("larger than 1 MB", str(r.context["form"].errors["attachments"]))

    @override_settings(ATTACHMENT_MAX_FILES=2)
    def test_too_many_files_rejected(self):
        self.client.force_login(self.manager)
        data = self.create_payload()
        data["attachments"] = [up("1.pdf"), up("2.pdf"), up("3.pdf")]
        r = self.client.post(reverse("task_create"), data)
        self.assertEqual(r.status_code, 200)
        self.assertIn("at most 2", str(r.context["form"].errors["attachments"]))

    def test_documents_are_optional(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.client.post(reverse("task_create"), self.create_payload()).status_code, 302)
        self.assertEqual(TaskAttachment.objects.count(), 0)

    def test_form_page_shows_label_and_plus(self):
        self.client.force_login(self.manager)
        html = self.client.get(reverse("task_create")).content.decode()
        self.assertIn("Attached documents", html)
        self.assertIn('enctype="multipart/form-data"', html)
        self.assertIn("data-attach", html)

    # ---- project
    def test_project_create_and_edit_with_documents(self):
        self.client.force_login(self.manager)
        html = self.client.get(reverse("project_create")).content.decode()
        self.assertIn("Attached documents", html)
        r = self.client.post(reverse("project_create"), {
            "name": "Beta", "code": "BET", "description": "", "is_active": "on",
            "attachments": [up("brief.pdf"), up("budget.xls")]})
        self.assertEqual(r.status_code, 302)
        p = ProjectAttachment.objects.first().project
        self.assertEqual(p.attachments.count(), 2)
        r = self.client.post(reverse("project_update", args=[p.pk]), {
            "name": "Beta", "code": "BET", "description": "", "is_active": "on", "attachments": [up("more.png")]})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(p.attachments.count(), 3)

    def test_project_documents_shown_and_downloadable_to_members_only(self):
        att = ProjectAttachment.objects.create(project=self.project, name="brief.pdf", size=4, uploaded_by=self.manager)
        att.file.save("brief.pdf", up("brief.pdf", b"%PDF-hello"), save=True)
        self.client.force_login(self.emp1)
        self.assertContains(self.client.get(reverse("project_detail", args=[self.project.pk])), "brief.pdf")
        r = self.client.get(reverse("attachment_download", args=["project", att.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment", r["Content-Disposition"])
        self.assertEqual(b"".join(r.streaming_content), b"%PDF-hello")
        self.client.force_login(self.other_manager)
        self.assertEqual(self.client.get(reverse("attachment_download", args=["project", att.pk])).status_code, 404)

    def test_project_document_delete_permissions(self):
        att = ProjectAttachment.objects.create(project=self.project, name="x.pdf", size=1, uploaded_by=self.manager)
        att.file.save("x.pdf", up("x.pdf"), save=True)
        url = reverse("attachment_delete", args=["project", att.pk])
        self.client.force_login(self.emp1)
        self.assertEqual(self.client.post(url).status_code, 403)
        self.client.force_login(self.manager)
        self.assertEqual(self.client.post(url).status_code, 302)
        self.assertFalse(ProjectAttachment.objects.exists())

    # ---- comments
    def comment(self, **data):
        return self.client.post(reverse("task_comment", args=[self.t1.pk]), data)

    def test_comment_with_text_and_documents(self):
        self.client.force_login(self.emp1)
        self.comment(body="See attached", attachments=[up("proof.png"), up("data.csv")])
        c = TaskComment.objects.get()
        self.assertEqual(c.body, "See attached")
        self.assertEqual(c.attachments.count(), 2)
        self.assertContains(self.client.get(reverse("task_detail", args=[self.t1.pk])), "proof.png")

    def test_comment_may_be_only_documents(self):
        self.client.force_login(self.emp1)
        self.comment(body="", attachments=[up("proof.pdf")])
        c = TaskComment.objects.get()
        self.assertEqual((c.body, c.attachments.count()), ("", 1))
        from notifications.models import Notification
        self.assertTrue(Notification.objects.filter(kind=Notification.Kind.COMMENT,
                                                    message__contains="attached documents").exists())

    def test_empty_comment_still_rejected(self):
        self.client.force_login(self.emp1)
        r = self.comment(body="  ")
        self.assertEqual(TaskComment.objects.count(), 0)
        self.assertEqual(r.status_code, 302)

    def test_bad_comment_document_rejected_and_no_comment_saved(self):
        self.client.force_login(self.emp1)
        self.comment(body="hi", attachments=[up("virus.exe")])
        self.assertEqual(TaskComment.objects.count(), 0)

    def test_comment_document_access_follows_task(self):
        self.client.force_login(self.emp1)
        self.comment(body="x", attachments=[up("a.pdf")])
        att = TaskCommentAttachment.objects.get()
        url = reverse("attachment_download", args=["comment", att.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.force_login(self.emp2)  # not their task
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(url).status_code, 200)
        # manager cannot delete someone else's comment document; the author can
        self.assertEqual(self.client.post(reverse("attachment_delete", args=["comment", att.pk])).status_code, 403)
        self.client.force_login(self.emp1)
        self.assertEqual(self.client.post(reverse("attachment_delete", args=["comment", att.pk])).status_code, 302)
        self.assertFalse(TaskCommentAttachment.objects.exists())

    def test_unknown_kind_is_404(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get("/tasks/attachments/bogus/1/").status_code, 404)

    # ---- task edit
    def test_manager_can_add_documents_when_editing_task(self):
        self.client.force_login(self.manager)
        data = self.payload()
        data["attachments"] = [up("late.pdf")]
        r = self.client.post(reverse("task_update", args=[self.t1.pk]), data)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.t1.attachments.get().name, "late.pdf")
        self.assertContains(self.client.get(reverse("task_detail", args=[self.t1.pk])), "late.pdf")


@override_settings(MEDIA_ROOT=TMP)
class MissingFileTests(BaseCase):
    def test_missing_worklog_file_redirects_instead_of_500(self):
        from decimal import Decimal
        from .models import TaskWorkLog
        log = TaskWorkLog.objects.create(task=self.t1, employee=self.emp1, work_date=self.t1.assign_date,
                                         duration_hours=Decimal("1"), description="x",
                                         attachment="worklogs/2026/10/gone.png")
        self.client.force_login(self.admin)
        r = self.client.get(reverse("worklog_attachment", args=[log.pk]), follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "missing from the server")

    def test_missing_new_attachment_file_redirects_instead_of_500(self):
        att = ProjectAttachment.objects.create(project=self.project, name="gone.pdf", size=1, file="attachments/gone.pdf")
        self.client.force_login(self.manager)
        r = self.client.get(reverse("attachment_download", args=["project", att.pk]))
        self.assertEqual(r.status_code, 302)
