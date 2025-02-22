import io
import textwrap
import unittest.mock as mock

import pytest

from lando.api.legacy.workers.landing_worker import (
    AUTOFORMAT_COMMIT_MESSAGE,
    LandingWorker,
)
from lando.main.models import SCM_LEVEL_3, Repo
from lando.main.models.landing_job import (
    LandingJob,
    LandingJobStatus,
    add_job_with_revisions,
)
from lando.main.models.revision import Revision
from lando.main.scm import SCM_TYPE_HG, HgSCM
from lando.main.scm.hg import LostPushRace
from lando.utils import HgPatchHelper


@pytest.fixture
@pytest.mark.django_db
def create_patch_revision(normal_patch):
    """A fixture that fake uploads a patch"""

    normal_patch_0 = normal_patch(0)

    def _create_patch_revision(number, patch=normal_patch_0):
        revision = Revision()
        revision.revision_id = number
        revision.diff_id = number
        revision.patch = patch
        revision.save()
        return revision

    return _create_patch_revision


LARGE_UTF8_THING = "😁" * 1000000

LARGE_PATCH = rf"""
# HG changeset patch
# User Test User <test@example.com>
# Date 0 0
#      Thu Jan 01 00:00:00 1970 +0000
# Diff Start Line 7
add another file.

diff --git a/test.txt b/test.txt
--- a/test.txt
+++ b/test.txt
@@ -1,1 +1,2 @@
 TEST
+{LARGE_UTF8_THING}
""".strip()

PATCH_WITHOUT_STARTLINE = r"""
# HG changeset patch
# User Test User <test@example.com>
# Date 0 0
#      Thu Jan 01 00:00:00 1970 +0000
add another file.
diff --git a/test.txt b/test.txt
--- a/test.txt
+++ b/test.txt
@@ -1,1 +1,2 @@
 TEST
+adding another line
""".strip()


PATCH_PUSH_LOSER = r"""
# HG changeset patch
# User Test User <test@example.com>
# Date 0 0
#      Thu Jan 01 00:00:00 1970 +0000
# Fail HG Import LOSE_PUSH_RACE
# Diff Start Line 8
add another file.
diff --git a/test.txt b/test.txt
--- a/test.txt
+++ b/test.txt
@@ -1,1 +1,2 @@
 TEST
+adding one more line again
""".strip()

PATCH_FORMATTING_PATTERN_PASS = r"""
# HG changeset patch
# User Test User <test@example.com>
# Date 0 0
#      Thu Jan 01 00:00:00 1970 +0000
# Diff Start Line 7
add formatting config

diff --git a/.lando.ini b/.lando.ini
new file mode 100644
--- /dev/null
+++ b/.lando.ini
@@ -0,0 +1,3 @@
+[autoformat]
+enabled = True
+
diff --git a/mach b/mach
new file mode 100755
--- /dev/null
+++ b/mach
@@ -0,0 +1,30 @@
+#!/usr/bin/env python3
+# This Source Code Form is subject to the terms of the Mozilla Public
+# License, v. 2.0. If a copy of the MPL was not distributed with this
+# file, You can obtain one at http://mozilla.org/MPL/2.0/.
+
+# Fake formatter that rewrites text to mOcKiNg cAse
+
+import pathlib
+import sys
+
+HERE = pathlib.Path(__file__).resolve().parent
+
+def split_chars(string) -> list:
+    return [char for char in string]
+
+
+if __name__ == "__main__":
+    testtxt = HERE / "test.txt"
+    if not testtxt.exists():
+        sys.exit(0)
+    with testtxt.open() as f:
+        stdin_content = f.read()
+    stdout_content = []
+
+    for i, word in enumerate(split_chars(stdin_content)):
+        stdout_content.append(word.upper() if i % 2 == 0 else word.lower())
+
+    with testtxt.open("w") as f:
+        f.write("".join(stdout_content))
+    sys.exit(0)

""".strip()

PATCH_FORMATTING_PATTERN_FAIL = r"""
# HG changeset patch
# User Test User <test@example.com>
# Date 0 0
#      Thu Jan 01 00:00:00 1970 +0000
# Diff Start Line 7
add formatting config

diff --git a/.lando.ini b/.lando.ini
new file mode 100644
--- /dev/null
+++ b/.lando.ini
@@ -0,0 +1,3 @@
+[autoformat]
+enabled = True
+
diff --git a/mach b/mach
new file mode 100755
--- /dev/null
+++ b/mach
@@ -0,0 +1,9 @@
+#!/usr/bin/env python3
+# This Source Code Form is subject to the terms of the Mozilla Public
+# License, v. 2.0. If a copy of the MPL was not distributed with this
+# file, You can obtain one at http://mozilla.org/MPL/2.0/.
+
+# Fake formatter that fails to run.
+import sys
+sys.exit("MACH FAILED")
+

""".strip()

PATCH_FORMATTED_1 = r"""
# HG changeset patch
# User Test User <test@example.com>
# Date 0 0
#      Thu Jan 01 00:00:00 1970 +0000
# Diff Start Line 7
bug 123: add another file for formatting 1

diff --git a/test.txt b/test.txt
--- a/test.txt
+++ b/test.txt
@@ -1,1 +1,4 @@
 TEST
+
+
+adding another line
""".strip()

PATCH_FORMATTED_2 = r"""
# HG changeset patch
# User Test User <test@example.com>
# Date 0 0
#      Thu Jan 01 00:00:00 1970 +0000
# Diff Start Line 7
add another file for formatting 2

diff --git a/test.txt b/test.txt
--- a/test.txt
+++ b/test.txt
@@ -2,3 +2,4 @@ TEST

 
 adding another line
+add one more line
""".strip()  # noqa: W293

TESTTXT_FORMATTED_1 = b"""
TeSt


aDdInG AnOtHeR LiNe
""".lstrip()

TESTTXT_FORMATTED_2 = b"""
TeSt


aDdInG AnOtHeR LiNe
aDd oNe mOrE LiNe
""".lstrip()


@pytest.mark.parametrize(
    "revisions_params",
    [
        [
            (1, {}),
            (2, {}),
        ],
        [(1, {"patch": LARGE_PATCH})],
    ],
)
@pytest.mark.django_db
def test_integrated_execute_job(
    hg_server,
    hg_clone,
    treestatusdouble,
    monkeypatch,
    create_patch_revision,
    revisions_params,
):
    treestatusdouble.open_tree("mozilla-central")
    repo = Repo.objects.create(
        scm_type=SCM_TYPE_HG,
        name="mozilla-central",
        url=hg_server,
        required_permission=SCM_LEVEL_3,
        push_path=hg_server,
        pull_path=hg_server,
        system_path=hg_clone.strpath,
    )
    revisions = [
        create_patch_revision(number, **kwargs) for number, kwargs in revisions_params
    ]
    job_params = {
        "status": LandingJobStatus.IN_PROGRESS,
        "requester_email": "test@example.com",
        "target_repo": repo,
        "attempts": 1,
    }
    job = add_job_with_revisions(revisions, **job_params)

    worker = LandingWorker(repos=Repo.objects.all(), sleep_seconds=0.01)

    # Mock `phab_trigger_repo_update` so we can make sure that it was called.
    mock_trigger_update = mock.MagicMock()
    monkeypatch.setattr(
        "lando.api.legacy.workers.landing_worker.LandingWorker.phab_trigger_repo_update",
        mock_trigger_update,
    )

    assert worker.run_job(job)
    assert job.status == LandingJobStatus.LANDED, job.error
    assert len(job.landed_commit_id) == 40
    assert (
        mock_trigger_update.call_count == 1
    ), "Successful landing should trigger Phab repo update."


@pytest.mark.django_db
def test_integrated_execute_job_with_force_push(
    hg_server,
    hg_clone,
    treestatusdouble,
    monkeypatch,
    create_patch_revision,
):
    treestatusdouble.open_tree("mozilla-central")
    repo = Repo.objects.create(
        scm_type=SCM_TYPE_HG,
        name="mozilla-central",
        url=hg_server,
        required_permission=SCM_LEVEL_3,
        push_path=hg_server,
        pull_path=hg_server,
        force_push=True,
        system_path=hg_clone.strpath,
    )
    scm = repo.scm
    job_params = {
        "status": LandingJobStatus.IN_PROGRESS,
        "requester_email": "test@example.com",
        "target_repo": repo,
        "attempts": 1,
    }
    job = add_job_with_revisions([create_patch_revision(1)], **job_params)

    worker = LandingWorker(repos=Repo.objects.all(), sleep_seconds=0.01)

    # We don't care about repo update in this test, however if we don't mock
    # this, the test will fail since there is no celery instance.
    monkeypatch.setattr(
        "lando.api.legacy.workers.landing_worker.LandingWorker.phab_trigger_repo_update",
        mock.MagicMock(),
    )

    scm.push = mock.MagicMock()
    assert worker.run_job(job)
    assert scm.push.call_count == 1
    assert len(scm.push.call_args) == 2
    assert len(scm.push.call_args[0]) == 1
    assert scm.push.call_args[0][0] == hg_server
    assert scm.push.call_args[1] == {"push_target": "", "force_push": True}


@pytest.mark.django_db
def test_integrated_execute_job_with_bookmark(
    hg_server,
    hg_clone,
    treestatusdouble,
    monkeypatch,
    create_patch_revision,
):
    treestatusdouble.open_tree("mozilla-central")
    repo = Repo.objects.create(
        scm_type=SCM_TYPE_HG,
        name="mozilla-central",
        url=hg_server,
        required_permission=SCM_LEVEL_3,
        push_path=hg_server,
        pull_path=hg_server,
        push_target="@",
        system_path=hg_clone.strpath,
    )
    scm = repo.scm
    job_params = {
        "status": LandingJobStatus.IN_PROGRESS,
        "requester_email": "test@example.com",
        "target_repo": repo,
        "attempts": 1,
    }
    job = add_job_with_revisions([create_patch_revision(1)], **job_params)

    worker = LandingWorker(repos=Repo.objects.all(), sleep_seconds=0.01)

    # We don't care about repo update in this test, however if we don't mock
    # this, the test will fail since there is no celery instance.
    monkeypatch.setattr(
        "lando.api.legacy.workers.landing_worker.LandingWorker.phab_trigger_repo_update",
        mock.MagicMock(),
    )

    scm.push = mock.MagicMock()
    assert worker.run_job(job)
    assert scm.push.call_count == 1
    assert len(scm.push.call_args) == 2
    assert len(scm.push.call_args[0]) == 1
    assert scm.push.call_args[0][0] == hg_server
    assert scm.push.call_args[1] == {"push_target": "@", "force_push": False}


@pytest.mark.django_db
def test_no_diff_start_line(
    hg_server,
    hg_clone,
    treestatusdouble,
    create_patch_revision,
    caplog,
):
    treestatusdouble.open_tree("mozilla-central")
    repo = Repo.objects.create(
        scm_type=SCM_TYPE_HG,
        name="mozilla-central",
        url=hg_server,
        required_permission=SCM_LEVEL_3,
        push_path=hg_server,
        pull_path=hg_server,
        system_path=hg_clone.strpath,
    )
    job_params = {
        "id": 1234,
        "status": LandingJobStatus.IN_PROGRESS,
        "requester_email": "test@example.com",
        "target_repo": repo,
        "attempts": 1,
    }
    job = add_job_with_revisions(
        [create_patch_revision(1, patch=PATCH_WITHOUT_STARTLINE)], **job_params
    )

    worker = LandingWorker(repos=Repo.objects.all(), sleep_seconds=0.01)

    assert worker.run_job(job)
    assert job.status == LandingJobStatus.FAILED
    assert "Patch without a diff start line." in caplog.text


@pytest.mark.django_db
def test_lose_push_race(
    monkeypatch,
    hg_server,
    hg_clone,
    treestatusdouble,
    create_patch_revision,
):
    treestatusdouble.open_tree("mozilla-central")
    repo = Repo.objects.create(
        scm_type=SCM_TYPE_HG,
        name="mozilla-central",
        url=hg_server,
        required_permission=SCM_LEVEL_3,
        push_path=hg_server,
        pull_path=hg_server,
        system_path=hg_clone.strpath,
    )
    scm = repo.scm
    job_params = {
        "id": 1234,
        "status": LandingJobStatus.IN_PROGRESS,
        "requester_email": "test@example.com",
        "target_repo": repo,
        "attempts": 1,
    }
    job = add_job_with_revisions(
        [create_patch_revision(1, patch=PATCH_PUSH_LOSER)], **job_params
    )

    mock_push = mock.MagicMock()
    mock_push.side_effect = (
        LostPushRace(["testing_args"], "testing_out", "testing_err", "testing_msg"),
    )
    monkeypatch.setattr(
        scm,
        "push",
        mock_push,
    )
    worker = LandingWorker(repos=Repo.objects.all(), sleep_seconds=0.01)

    assert not worker.run_job(job)
    assert job.status == LandingJobStatus.DEFERRED


@pytest.mark.django_db
def test_merge_conflict(
    hg_server,
    hg_clone,
    treestatusdouble,
    monkeypatch,
    create_patch_revision,
    normal_patch,
    caplog,
):
    treestatusdouble.open_tree("mozilla-central")
    repo = Repo.objects.create(
        scm_type=SCM_TYPE_HG,
        name="mozilla-central",
        url=hg_server,
        required_permission=SCM_LEVEL_3,
        push_path=hg_server,
        pull_path=hg_server,
        system_path=hg_clone.strpath,
    )
    job_params = {
        "id": 1234,
        "status": LandingJobStatus.IN_PROGRESS,
        "requester_email": "test@example.com",
        "target_repo": repo,
        "attempts": 1,
    }
    job = add_job_with_revisions(
        [
            create_patch_revision(1, patch=PATCH_FORMATTED_2),
        ],
        **job_params,
    )

    worker = LandingWorker(repos=Repo.objects.all(), sleep_seconds=0.01)

    # We don't care about repo update in this test, however if we don't mock
    # this, the test will fail since there is no celery instance.
    monkeypatch.setattr(
        "lando.api.legacy.workers.landing_worker.LandingWorker.phab_trigger_repo_update",
        mock.MagicMock(),
    )

    assert worker.run_job(job)
    assert job.status == LandingJobStatus.FAILED
    assert "hunks FAILED" in caplog.text
    assert job.error_breakdown, "No error breakdown added to job"
    assert job.error_breakdown.get(
        "rejects_paths"
    ), "Empty or missing reject information in error breakdown"
    failed_paths = [p["path"] for p in job.error_breakdown["failed_paths"]]
    assert set(failed_paths) == set(
        job.error_breakdown["rejects_paths"].keys()
    ), "Mismatch between failed_paths and rejects_paths"
    for fp in failed_paths:
        assert job.error_breakdown["rejects_paths"][fp].get(
            "path"
        ), f"Empty or missing reject path for failed path {fp}"
        assert job.error_breakdown["rejects_paths"][fp].get(
            "content"
        ), f"Empty or missing reject content for failed path {fp}"


@pytest.mark.django_db
def test_failed_landing_job_notification(
    hg_server,
    hg_clone,
    treestatusdouble,
    monkeypatch,
    create_patch_revision,
):
    """Ensure that a failed landings triggers a user notification."""
    treestatusdouble.open_tree("mozilla-central")
    repo = Repo.objects.create(
        scm_type=SCM_TYPE_HG,
        name="mozilla-central",
        required_permission=SCM_LEVEL_3,
        push_path=hg_server,
        pull_path=hg_server,
        approval_required=True,
        autoformat_enabled=False,
        system_path=hg_clone.strpath,
    )

    scm = repo.scm

    # Mock `scm.update_repo` so we can force a failed landing.
    mock_update_repo = mock.MagicMock()
    mock_update_repo.side_effect = Exception("Forcing a failed landing")
    monkeypatch.setattr(scm, "update_repo", mock_update_repo)

    revisions = [
        create_patch_revision(1),
        create_patch_revision(2),
    ]
    job_params = {
        "status": LandingJobStatus.IN_PROGRESS,
        "requester_email": "test@example.com",
        "target_repo": repo,
        "attempts": 1,
    }
    job = add_job_with_revisions(revisions, **job_params)

    worker = LandingWorker(repos=Repo.objects.all(), sleep_seconds=0.01)

    # Mock `notify_user_of_landing_failure` so we can make sure that it was called.
    mock_notify = mock.MagicMock()
    monkeypatch.setattr(
        "lando.api.legacy.workers.landing_worker.notify_user_of_landing_failure",
        mock_notify,
    )

    assert worker.run_job(job)
    assert job.status == LandingJobStatus.FAILED
    assert mock_notify.call_count == 1


def test_landing_worker__extract_error_data():
    exception_message = textwrap.dedent(
        """\
    patching file toolkit/moz.configure
    Hunk #1 FAILED at 2075
    Hunk #2 FAILED at 2325
    Hunk #3 FAILED at 2340
    3 out of 3 hunks FAILED -- saving rejects to file toolkit/moz.configure.rej
    patching file moz.configure
    Hunk #1 FAILED at 239
    Hunk #2 FAILED at 250
    2 out of 2 hunks FAILED -- saving rejects to file moz.configure.rej
    patching file a/b/c.d
    Hunk #1 FAILED at 656
    1 out of 1 hunks FAILED -- saving rejects to file a/b/c.d.rej
    patching file d/e/f.g
    Hunk #1 FAILED at 6
    1 out of 1 hunks FAILED -- saving rejects to file d/e/f.g.rej
    patching file h/i/j.k
    Hunk #1 FAILED at 4
    1 out of 1 hunks FAILED -- saving rejects to file h/i/j.k.rej
    file G0fvb1RuMQxXNjs already exists
    1 out of 1 hunks FAILED -- saving rejects to file G0fvb1RuMQxXNjs.rej
    unable to find 'abc/def' for patching
    (use '--prefix' to apply patch relative to the current directory)
    1 out of 1 hunks FAILED -- saving rejects to file abc/def.rej
    patching file browser/locales/en-US/browser/browserContext.ftl
    Hunk #1 succeeded at 300 with fuzz 2 (offset -4 lines).
    abort: patch failed to apply"""
    )

    expected_failed_paths = [
        "toolkit/moz.configure",
        "moz.configure",
        "a/b/c.d",
        "d/e/f.g",
        "h/i/j.k",
        "G0fvb1RuMQxXNjs",
        "abc/def",
    ]

    expected_rejects_paths = [
        "toolkit/moz.configure.rej",
        "moz.configure.rej",
        "a/b/c.d.rej",
        "d/e/f.g.rej",
        "h/i/j.k.rej",
        "G0fvb1RuMQxXNjs.rej",
        "abc/def.rej",
    ]

    failed_paths, rejects_paths = LandingWorker.extract_error_data(exception_message)
    assert failed_paths == expected_failed_paths
    assert rejects_paths == expected_rejects_paths


@pytest.mark.django_db
def test_format_patch_success_unchanged(
    hg_server,
    hg_clone,
    treestatusdouble,
    monkeypatch,
    create_patch_revision,
    normal_patch,
):
    """Tests automated formatting happy path where formatters made no changes."""
    tree = "mozilla-central"
    treestatusdouble.open_tree(tree)
    repo = Repo.objects.create(
        scm_type=SCM_TYPE_HG,
        name=tree,
        url=hg_server,
        push_path=hg_server,
        pull_path=hg_server,
        required_permission=SCM_LEVEL_3,
        autoformat_enabled=True,
        system_path=hg_clone.strpath,
    )

    revisions = [
        create_patch_revision(1, patch=PATCH_FORMATTING_PATTERN_PASS),
        create_patch_revision(2, patch=normal_patch(2)),
    ]
    job_params = {
        "status": LandingJobStatus.IN_PROGRESS,
        "requester_email": "test@example.com",
        "target_repo": repo,
        "attempts": 1,
    }
    job = add_job_with_revisions(revisions, **job_params)

    worker = LandingWorker(repos=Repo.objects.all(), sleep_seconds=0.01)

    # Mock `phab_trigger_repo_update` so we can make sure that it was called.
    mock_trigger_update = mock.MagicMock()
    monkeypatch.setattr(
        "lando.api.legacy.workers.landing_worker.LandingWorker.phab_trigger_repo_update",
        mock_trigger_update,
    )

    assert worker.run_job(job)
    assert (
        job.status == LandingJobStatus.LANDED
    ), "Successful landing should set `LANDED` status."
    assert (
        mock_trigger_update.call_count == 1
    ), "Successful landing should trigger Phab repo update."
    assert (
        job.formatted_replacements is None
    ), "Autoformat making no changes should leave `formatted_replacements` empty."


@pytest.mark.django_db
def test_format_single_success_changed(
    hg_server,
    hg_clone,
    treestatusdouble,
    monkeypatch,
    create_patch_revision,
):
    """Test formatting a single commit via amending."""
    tree = "mozilla-central"
    treestatusdouble.open_tree(tree)
    repo = Repo.objects.create(
        scm_type=SCM_TYPE_HG,
        name=tree,
        url=hg_server,
        push_path=hg_server,
        pull_path=hg_server,
        required_permission=SCM_LEVEL_3,
        autoformat_enabled=True,
        system_path=hg_clone.strpath,
    )

    # Push the `mach` formatting patch.
    hgrepo = HgSCM(hg_clone.strpath)
    with hgrepo.for_push("test@example.com"):
        ph = HgPatchHelper(io.StringIO(PATCH_FORMATTING_PATTERN_PASS))
        hgrepo.apply_patch(
            ph.get_diff(),
            ph.get_commit_description(),
            ph.get_header("User"),
            ph.get_header("Date"),
        )
        hgrepo.push(repo.push_path)
        pre_landing_tip = hgrepo.run_hg(["log", "-r", "tip", "-T", "{node}"]).decode(
            "utf-8"
        )

    # Upload a patch for formatting.
    job_params = {
        "status": LandingJobStatus.IN_PROGRESS,
        "requester_email": "test@example.com",
        "target_repo": repo,
        "attempts": 1,
    }
    job = add_job_with_revisions(
        [create_patch_revision(2, patch=PATCH_FORMATTED_1)], **job_params
    )

    worker = LandingWorker(repos=Repo.objects.all(), sleep_seconds=0.01)

    # Mock `phab_trigger_repo_update` so we can make sure that it was called.
    mock_trigger_update = mock.MagicMock()
    monkeypatch.setattr(
        "lando.api.legacy.workers.landing_worker.LandingWorker.phab_trigger_repo_update",
        mock_trigger_update,
    )

    assert worker.run_job(job), "`run_job` should return `True` on a successful run."
    assert (
        job.status == LandingJobStatus.LANDED
    ), "Successful landing should set `LANDED` status."
    assert (
        mock_trigger_update.call_count == 1
    ), "Successful landing should trigger Phab repo update."

    with hgrepo.for_push(job.requester_email):
        repo_root = hgrepo.run_hg(["root"]).decode("utf-8").strip()

        # Get the commit message.
        desc = hgrepo.run_hg(["log", "-r", "tip", "-T", "{desc}"]).decode("utf-8")

        # Get the content of the file after autoformatting.
        tip_content = hgrepo.run_hg(
            ["cat", "--cwd", repo_root, "-r", "tip", "test.txt"]
        )

        # Get the hash behind the tip commit.
        hash_behind_current_tip = hgrepo.run_hg(
            ["log", "-r", "tip^", "-T", "{node}"]
        ).decode("utf-8")

    assert tip_content == TESTTXT_FORMATTED_1, "`test.txt` is incorrect in base commit."

    assert (
        desc == "bug 123: add another file for formatting 1"
    ), "Autoformat via amend should not change commit message."

    assert (
        hash_behind_current_tip == pre_landing_tip
    ), "Autoformat via amending should only land a single commit."


@pytest.mark.django_db
def test_format_stack_success_changed(
    hg_server,
    hg_clone,
    treestatusdouble,
    monkeypatch,
    create_patch_revision,
):
    """Test formatting a stack via an autoformat tip commit."""
    tree = "mozilla-central"
    treestatusdouble.open_tree(tree)
    repo = Repo.objects.create(
        scm_type=SCM_TYPE_HG,
        name=tree,
        url=hg_server,
        push_path=hg_server,
        pull_path=hg_server,
        required_permission=SCM_LEVEL_3,
        autoformat_enabled=True,
        system_path=hg_clone.strpath,
    )

    hgrepo = HgSCM(hg_clone.strpath)

    revisions = [
        create_patch_revision(1, patch=PATCH_FORMATTING_PATTERN_PASS),
        create_patch_revision(2, patch=PATCH_FORMATTED_1),
        create_patch_revision(3, patch=PATCH_FORMATTED_2),
    ]
    job_params = {
        "status": LandingJobStatus.IN_PROGRESS,
        "requester_email": "test@example.com",
        "target_repo": repo,
        "attempts": 1,
    }
    job = add_job_with_revisions(revisions, **job_params)

    worker = LandingWorker(repos=Repo.objects.all(), sleep_seconds=0.01)

    # Mock `phab_trigger_repo_update` so we can make sure that it was called.
    mock_trigger_update = mock.MagicMock()
    monkeypatch.setattr(
        "lando.api.legacy.workers.landing_worker.LandingWorker.phab_trigger_repo_update",
        mock_trigger_update,
    )

    assert worker.run_job(job), "`run_job` should return `True` on a successful run."
    assert (
        job.status == LandingJobStatus.LANDED
    ), "Successful landing should set `LANDED` status."
    assert (
        mock_trigger_update.call_count == 1
    ), "Successful landing should trigger Phab repo update."

    with hgrepo.for_push(job.requester_email):
        repo_root = hgrepo.run_hg(["root"]).decode("utf-8").strip()

        # Get the commit message.
        desc = hgrepo.run_hg(["log", "-r", "tip", "-T", "{desc}"]).decode("utf-8")

        # Get the content of the file after autoformatting.
        rev3_content = hgrepo.run_hg(
            ["cat", "--cwd", repo_root, "-r", "tip", "test.txt"]
        )

    assert (
        rev3_content == TESTTXT_FORMATTED_2
    ), "`test.txt` is incorrect in base commit."

    assert (
        "# ignore-this-changeset" in desc
    ), "Commit message for autoformat commit should contain `# ignore-this-changeset`."

    assert desc == AUTOFORMAT_COMMIT_MESSAGE.format(
        bugs="Bug 123"
    ), "Autoformat commit has incorrect commit message."


@pytest.mark.django_db
def test_format_patch_fail(
    hg_server,
    hg_clone,
    treestatusdouble,
    monkeypatch,
    create_patch_revision,
):
    """Tests automated formatting failures before landing."""
    tree = "mozilla-central"
    treestatusdouble.open_tree(tree)
    repo = Repo.objects.create(
        scm_type=SCM_TYPE_HG,
        name=tree,
        required_permission=SCM_LEVEL_3,
        url=hg_server,
        push_path=hg_server,
        pull_path=hg_server,
        autoformat_enabled=True,
        system_path=hg_clone.strpath,
    )

    revisions = [
        create_patch_revision(1, patch=PATCH_FORMATTING_PATTERN_FAIL),
        create_patch_revision(2),
        create_patch_revision(3),
    ]
    job_params = {
        "status": LandingJobStatus.IN_PROGRESS,
        "requester_email": "test@example.com",
        "target_repo": repo,
        "attempts": 1,
    }
    job = add_job_with_revisions(revisions, **job_params)

    worker = LandingWorker(repos=Repo.objects.all(), sleep_seconds=0.01)

    # Mock `notify_user_of_landing_failure` so we can make sure that it was called.
    mock_notify = mock.MagicMock()
    monkeypatch.setattr(
        "lando.api.legacy.workers.landing_worker.notify_user_of_landing_failure",
        mock_notify,
    )

    assert not worker.run_job(
        job
    ), "`run_job` should return `False` when autoformatting fails."
    assert (
        job.status == LandingJobStatus.FAILED
    ), "Failed autoformatting should set `FAILED` job status."
    assert (
        "Lando failed to format your patch" in job.error
    ), "Error message is not set to show autoformat caused landing failure."
    assert (
        mock_notify.call_count == 1
    ), "User should be notified their landing was unsuccessful due to autoformat."


@pytest.mark.django_db
def test_format_patch_no_landoini(
    hg_server,
    hg_clone,
    treestatusdouble,
    monkeypatch,
    create_patch_revision,
):
    """Tests behaviour of Lando when the `.lando.ini` file is missing."""
    treestatusdouble.open_tree("mozilla-central")
    repo = Repo.objects.create(
        scm_type=SCM_TYPE_HG,
        name="mozilla-central",
        required_permission=SCM_LEVEL_3,
        url=hg_server,
        push_path=hg_server,
        pull_path=hg_server,
        autoformat_enabled=True,
        system_path=hg_clone.strpath,
    )

    revisions = [
        create_patch_revision(1),
        create_patch_revision(2),
    ]
    job_params = {
        "status": LandingJobStatus.IN_PROGRESS,
        "requester_email": "test@example.com",
        "target_repo": repo,
        "attempts": 1,
    }
    job = add_job_with_revisions(revisions, **job_params)

    worker = LandingWorker(repos=Repo.objects.all(), sleep_seconds=0.01)

    # Mock `phab_trigger_repo_update` so we can make sure that it was called.
    mock_trigger_update = mock.MagicMock()
    monkeypatch.setattr(
        "lando.api.legacy.workers.landing_worker.LandingWorker.phab_trigger_repo_update",
        mock_trigger_update,
    )

    # Mock `notify_user_of_landing_failure` so we can make sure that it was called.
    mock_notify = mock.MagicMock()
    monkeypatch.setattr(
        "lando.api.legacy.workers.landing_worker.notify_user_of_landing_failure",
        mock_notify,
    )

    assert worker.run_job(job)
    assert (
        job.status == LandingJobStatus.LANDED
    ), "Missing `.lando.ini` should not inhibit landing."
    assert (
        mock_notify.call_count == 0
    ), "Should not notify user of landing failure due to `.lando.ini` missing."
    assert (
        mock_trigger_update.call_count == 1
    ), "Successful landing should trigger Phab repo update."


# bug 1893453
@pytest.mark.xfail
@pytest.mark.django_db
def test_landing_job_revisions_sorting(
    create_patch_revision,
):
    revisions = [
        create_patch_revision(1),
        create_patch_revision(2),
        create_patch_revision(3),
    ]
    job_params = {
        "status": LandingJobStatus.SUBMITTED,
        "requester_email": "test@example.com",
        "repository_name": "mozilla-central",
        "attempts": 1,
    }
    job = add_job_with_revisions(revisions, **job_params)

    assert list(job.revisions.all()) == revisions
    new_ordering = [revisions[2], revisions[0], revisions[1]]
    job.sort_revisions(new_ordering)
    job.save()
    job = LandingJob.objects.get(id=job.id)
    assert list(job.revisions.all()) == new_ordering
