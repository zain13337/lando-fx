# Generated by Django 5.1.3 on 2024-12-13 02:11

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("main", "0010_alter_repo_system_path"),
    ]

    operations = [
        migrations.RenameField(
            model_name="repo",
            old_name="scm",
            new_name="scm_type",
        ),
    ]