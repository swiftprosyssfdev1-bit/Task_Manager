from django.urls import reverse

from .models import Task
from .tests import BaseCase


class PriorityColourTests(BaseCase):
    def test_priority_dropdowns_are_marked_for_colouring(self):
        self.client.force_login(self.manager)
        r = self.client.get(reverse("task_create"))
        self.assertIn("priority-select", r.context["form"].fields["priority"].widget.attrs["class"])
        self.assertContains(r, 'class="form-select priority-select"')
        for value in ("low", "medium", "high", "critical"):
            self.assertContains(r, f'<option value="{value}"')
        r = self.client.get(reverse("task_update", args=[self.t1.pk]))
        self.assertIn("priority-select", r.context["form"].fields["priority"].widget.attrs["class"])
        r = self.client.get(reverse("task_list"))
        self.assertIn("priority-select", r.context["filter_form"].fields["priority"].widget.attrs["class"])

    def test_script_is_loaded_and_exists(self):
        self.client.force_login(self.manager)
        self.assertContains(self.client.get(reverse("task_create")), "js/priority.js")
        from django.contrib.staticfiles import finders
        self.assertTrue(finders.find("js/priority.js"))
        css = open(finders.find("css/app.css")).read()
        for value in ("low", "medium", "high", "critical"):
            self.assertIn(f'.prio-dot[data-value="{value}"]', css)
        self.assertNotIn("priority-select option", css)  # no coloured rows in the list, only dots

    def test_priority_badge_colours(self):
        expected = {"low": "success", "medium": "warning", "high": "orange", "critical": "danger"}
        self.client.force_login(self.manager)
        for priority, colour in expected.items():
            Task.objects.filter(pk=self.t1.pk).update(priority=priority)
            r = self.client.get(reverse("task_detail", args=[self.t1.pk]))
            self.assertContains(r, f'badge text-bg-{colour}">{priority.capitalize()} priority')
