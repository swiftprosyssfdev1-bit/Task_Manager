"""Shared helpers for attached documents on projects, tasks and comments.

Allowed: Excel, PDF, images, ZIP files, and folders (a folder is uploaded as the files inside it,
each keeping its relative path as its name). Files are never served from a public URL; the
download view checks access first.
"""
import os
import posixpath
import uuid

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import models
from django.utils import timezone


def allowed_extensions():
    return sorted({e for group in settings.ATTACHMENT_EXTENSIONS.values() for e in group})


def accept_attribute():
    return ",".join("." + e for e in allowed_extensions())


def allowed_labels():
    return ", ".join(settings.ATTACHMENT_EXTENSIONS.keys())


def attachment_upload_to(instance, filename):
    """Random stored name: the original name lives in `name`, so uploads can never overwrite each other."""
    ext = os.path.splitext(filename)[1].lower()
    now = timezone.now()
    return f"attachments/{now:%Y/%m}/{uuid.uuid4().hex}{ext}"


class AttachmentBase(models.Model):
    file = models.FileField(upload_to=attachment_upload_to, max_length=255)
    name = models.CharField(max_length=500, help_text="Original file name, or folder/path for files from a folder.")
    size = models.PositiveBigIntegerField(default=0)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True
        ordering = ["name", "id"]

    def __str__(self):
        return self.name

    @property
    def folder(self):
        """Folder part of the name ('' for a single file)."""
        return posixpath.dirname(self.name)

    @property
    def basename(self):
        return posixpath.basename(self.name)

    @property
    def extension(self):
        return os.path.splitext(self.name)[1].lower().lstrip(".")

    @property
    def is_image(self):
        return self.extension in settings.ATTACHMENT_EXTENSIONS["Image"]

    @property
    def icon(self):
        ext = self.extension
        if ext in settings.ATTACHMENT_EXTENSIONS["Excel"]:
            return "bi-file-earmark-excel"
        if ext == "pdf":
            return "bi-file-earmark-pdf"
        if ext in settings.ATTACHMENT_EXTENSIONS["Image"]:
            return "bi-file-earmark-image"
        if ext == "zip":
            return "bi-file-earmark-zip"
        return "bi-file-earmark"

    @property
    def size_label(self):
        size = float(self.size)
        for unit in ("B", "KB", "MB"):
            if size < 1024:
                return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} GB"


def clean_display_name(raw, fallback):
    """A safe relative name such as 'Specs/v2/plan.pdf'. Never absolute, never contains '..'."""
    raw = (raw or "").replace("\\", "/")
    parts = [p for p in raw.split("/") if p not in ("", ".", "..")]
    name = "/".join(parts) or os.path.basename(fallback or "") or "file"
    return name[-500:]


def validate_uploads(files, paths=None):
    """Returns [(UploadedFile, display_name)] or raises ValidationError listing every problem."""
    files = [f for f in (files or []) if f]
    if not files:
        return []
    paths = list(paths or [])
    if len(paths) != len(files):
        paths = [""] * len(files)  # no usable folder information: use the plain file names
    limit_files, limit_bytes = settings.ATTACHMENT_MAX_FILES, settings.ATTACHMENT_MAX_MB * 1024 * 1024
    errors, items = [], []
    if len(files) > limit_files:
        raise ValidationError(f"You can attach at most {limit_files} files at a time (you chose {len(files)}). "
                              "Put the rest in a ZIP file.")
    ok_ext = set(allowed_extensions())
    for f, raw in zip(files, paths):
        name = clean_display_name(raw, f.name)
        ext = os.path.splitext(name)[1].lower().lstrip(".")
        if ext not in ok_ext:
            errors.append(f"{name}: type not allowed")
        elif f.size > limit_bytes:
            errors.append(f"{name}: larger than {settings.ATTACHMENT_MAX_MB} MB")
        else:
            items.append((f, name))
    if errors:
        shown = errors[:5] + ([f"... and {len(errors) - 5} more"] if len(errors) > 5 else [])
        raise ValidationError([f"Allowed documents: {allowed_labels()}, folders and ZIP files."] + shown)
    return items


def save_attachments(model, fk_name, owner, items, user):
    """Store the validated items on `owner`. Each call writes its own copy of every file."""
    created = []
    for f, name in items:
        f.seek(0)
        obj = model(**{fk_name: owner}, name=name, size=f.size, uploaded_by=user)
        obj.file.save(os.path.basename(name), ContentFile(f.read()), save=False)
        created.append(obj)
    if created:
        model.objects.bulk_create(created)
    return created


class MultiFileInput(forms.FileInput):
    allow_multiple_selected = True


class MultiFileField(forms.FileField):
    widget = MultiFileInput

    def to_python(self, data):
        return [f for f in (data or []) if f]

    def clean(self, data, initial=None):
        data = self.to_python(data)
        if self.required and not data:
            raise ValidationError(self.error_messages["required"], code="required")
        return data

    def has_changed(self, initial, data):
        return bool(data)


class AttachmentFormMixin:
    """Adds an 'attachments' field (labelled 'Attached documents'). Folder paths arrive in 'attachment_paths'."""
    attachment_model = None
    attachment_fk = None

    def _add_attachment_field(self):
        self.fields["attachments"] = MultiFileField(
            required=False, label="Attached documents",
            help_text=(f"{allowed_labels()}, folders and ZIP files. Up to {settings.ATTACHMENT_MAX_MB} MB each."))
        self.fields["attachments"].widget.attrs.update({
            "accept": accept_attribute(), "hidden": True, "data-max-mb": settings.ATTACHMENT_MAX_MB,
            "data-max-files": settings.ATTACHMENT_MAX_FILES, "data-labels": allowed_labels()})
        self.order_fields(getattr(self, "field_order", None))

    def clean_attachments(self):
        files = self.cleaned_data.get("attachments") or []
        return validate_uploads(files, self.data.getlist("attachment_paths") if hasattr(self.data, "getlist") else [])

    def save_attachments_to(self, owners, user):
        """Attach the chosen documents to each owner (one copy per owner)."""
        items = self.cleaned_data.get("attachments") or []
        for owner in owners:
            save_attachments(self.attachment_model, self.attachment_fk, owner, items, user)
