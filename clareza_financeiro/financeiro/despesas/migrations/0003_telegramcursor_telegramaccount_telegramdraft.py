# Cria as tabelas da integração ao executar `python manage.py migrate`.
# TelegramAccount associa o usuário do Clareza ao Telegram e guarda o hash do
# código de vínculo; TelegramDraft mantém os dados em revisão e o token do arquivo;
# TelegramCursor registra de qual evento o polling deve continuar após reiniciar.
# Esta migração acrescenta tabelas sem alterar as despesas já cadastradas.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('despesas', '0002_extraction_fields'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='TelegramCursor',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('offset', models.BigIntegerField(default=0)),
            ],
        ),
        migrations.CreateModel(
            name='TelegramAccount',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('telegram_id', models.BigIntegerField(blank=True, null=True, unique=True)),
                ('code_hash', models.CharField(blank=True, max_length=64)),
                ('code_expires_at', models.DateTimeField(blank=True, null=True)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='TelegramDraft',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('token', models.TextField()),
                ('fields', models.JSONField(default=dict)),
                ('warnings', models.JSONField(default=list)),
                ('editing', models.CharField(blank=True, max_length=30)),
                ('expires_at', models.DateTimeField()),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
        ),
    ]
