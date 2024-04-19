"""
This module provides the definitions for custom revision/diff warnings.

The `DiffWarning` model provides a warning that is associated with a particular
Phabricator diff that is associated with a particular revision.
"""

from __future__ import annotations

import logging
from typing import Any

from django.db import models
from django.utils.translation import gettext_lazy

from lando.main.models import BaseModel
from lando.utils import build_patch_for_revision

logger = logging.getLogger(__name__)


class RevisionLandingJob(BaseModel):
    landing_job = models.ForeignKey("LandingJob", on_delete=models.SET_NULL, null=True)
    revision = models.ForeignKey("Revision", on_delete=models.SET_NULL, null=True)
    index = models.IntegerField(null=True, blank=True)
    diff_id = models.IntegerField(null=True, blank=True)


class Revision(BaseModel):
    """
    A representation of a revision in the database.

    Includes a reference to the related Phabricator revision and diff ID if one exists.
    """

    # revision_id and diff_id map to Phabricator IDs (integers).
    revision_id = models.IntegerField(blank=True, null=True, unique=True)

    # diff_id is that of the latest diff on the revision at landing request time. It
    # does not track all diffs.
    diff_id = models.IntegerField(blank=True, null=True)

    # The actual patch.
    patch = models.TextField(blank=True, default="")

    # Patch metadata, such as author, timestamp, etc...
    patch_data = models.JSONField(blank=True, default=dict)

    # A general purpose data field to store arbitrary information about this revision.
    data = models.JSONField(blank=True, default=dict)

    # The commit ID generated by the landing worker, before pushing to remote repo.
    commit_id = models.CharField(max_length=40, null=True, blank=True)

    def __repr__(self) -> str:
        """Return a human-readable representation of the instance."""
        # Add an identifier for the Phabricator revision if it exists.
        phab_identifier = (
            f" [D{self.revision_id}-{self.diff_id}]>" if self.revision_id else ""
        )
        return f"<{self.__class__.__name__}: {self.id}{phab_identifier}>"

    @property
    def patch_bytes(self) -> bytes:
        return self.patch.encode("utf-8")

    @property
    def patch_string(self) -> str:
        """Return the patch as a UTF-8 encoded string."""
        # Here for compatiblity, as an alias.
        # TODO: remove this in the near future.
        return self.patch

    @classmethod
    def get_from_revision_id(cls, revision_id: int) -> "Revision" | None:
        """Return a Revision object from a given ID."""
        if cls.objects.filter(revision_id=revision_id).exists():
            return cls.objects.get(revision_id=revision_id)

    @classmethod
    def new_from_patch(cls, raw_diff: str, patch_data: dict[str, str]) -> Revision:
        """Construct a new Revision from patch data."""
        rev = Revision()
        rev.set_patch(raw_diff, patch_data)
        rev.save()
        return rev

    def set_patch(self, raw_diff: str, patch_data: dict[str, str]):
        """Given a raw_diff and patch data, build the patch and store it."""
        self.patch_data = patch_data
        patch = build_patch_for_revision(raw_diff, **self.patch_data)
        self.patch = patch

    def serialize(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "revision_id": self.revision_id,
            "diff_id": self.diff_id,
            "landing_jobs": [job.id for job in self.landing_jobs],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class DiffWarningStatus(models.TextChoices):
    ACTIVE = "ACTIVE", gettext_lazy("Active")
    ARCHIVED = "ARCHIVED", gettext_lazy("Archived")


class DiffWarningGroup(models.TextChoices):
    GENERAL = "GENERAL", gettext_lazy("General")
    LINT = "LINT", gettext_lazy("Lint")


class DiffWarning(BaseModel):
    """Represents a warning message associated with a particular diff and revision."""

    # A Phabricator revision and diff ID (NOTE: revision ID does not include a prefix.)
    revision_id = models.IntegerField()
    diff_id = models.IntegerField()

    # An arbitary dictionary of data that will be determined by the client.
    # It is up to the UI to interpret this data and show it to the user.
    error_breakdown = models.JSONField(null=False, blank=True, default=dict)

    # Whether the warning is active or archived. This is used in filters.
    status = models.CharField(
        max_length=12,
        choices=DiffWarningStatus,
        default=DiffWarningStatus.ACTIVE,
        null=False,
        blank=True,
    )

    # The "type" of warning. This is mainly to group warnings when querying the API.
    group = models.CharField(
        max_length=12,
        choices=DiffWarningGroup,
        null=False,
        blank=False,
    )

    def serialize(self):
        """Return a JSON serializable dictionary."""
        return {
            "id": self.id,
            "diff_id": self.diff_id,
            "revision_id": self.revision_id,
            "status": self.status.value,
            "group": self.group.value,
            "data": self.data,
        }