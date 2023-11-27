# Generated by Django 5.0b1 on 2023-11-27 16:43

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("main", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="Repo",
            fields=[
                (
                    "basemodel_ptr",
                    models.OneToOneField(
                        auto_created=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        parent_link=True,
                        primary_key=True,
                        serialize=False,
                        to="main.basemodel",
                    ),
                ),
                ("name", models.CharField(max_length=255, unique=True)),
                ("default_branch", models.CharField(default="main", max_length=255)),
                ("url", models.CharField(max_length=255)),
                ("push_path", models.CharField(max_length=255)),
                ("pull_path", models.CharField(max_length=255)),
                ("is_initialized", models.BooleanField(default=False)),
                (
                    "system_path",
                    models.FilePathField(
                        allow_folders=True, max_length=255, path="/mediafiles/repos"
                    ),
                ),
            ],
            bases=("main.basemodel",),
        ),
        migrations.CreateModel(
            name="Worker",
            fields=[
                (
                    "basemodel_ptr",
                    models.OneToOneField(
                        auto_created=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        parent_link=True,
                        primary_key=True,
                        serialize=False,
                        to="main.basemodel",
                    ),
                ),
                ("name", models.CharField(max_length=255, unique=True)),
                ("is_paused", models.BooleanField(default=False)),
                ("is_stopped", models.BooleanField(default=False)),
                ("ssh_private_key", models.TextField(blank=True, null=True)),
                ("throttle_seconds", models.IntegerField(default=10)),
                ("sleep_seconds", models.IntegerField(default=10)),
                ("applicable_repos", models.ManyToManyField(to="main.repo")),
            ],
            bases=("main.basemodel",),
        ),
    ]