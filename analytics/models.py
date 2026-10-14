from django.db import models


class Holiday(models.Model):
    """Non-working days. Used only to exclude days from business-day calculations."""
    date = models.DateField(unique=True)
    name = models.CharField(max_length=120)

    class Meta:
        ordering = ["date"]
        verbose_name = "holiday"

    def __str__(self):
        return f"{self.date:%d %b %Y} {self.name}"
