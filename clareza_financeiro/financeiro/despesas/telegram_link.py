"""Gerencia a vinculação da conta do Clareza à identidade do Telegram.

Atende à página autenticada de configuração: gera um código temporário de uso
único, guarda seu hash e permite desfazer o vínculo. O código é consumido pelo
bot em conversa privada para associar o identificador numérico ao usuário.
Também descarta o cadastro pendente e seu arquivo ao desvincular a conta.
"""
from datetime import timedelta
import hashlib
import secrets

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from .document_extraction import DocumentError, TemporaryUploadStore
from .models import TelegramAccount, TelegramDraft


def discard_draft(draft):
    try:
        TemporaryUploadStore().delete(draft.token, draft.user_id)
    except DocumentError:
        pass  # Expired files are removed by the existing hourly cleanup.
    draft.delete()


@login_required
@never_cache
@require_http_methods(['GET', 'POST'])
def telegram_settings(request):
    code = None
    configured = bool(settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_BOT_USERNAME)
    with transaction.atomic():
        account, _ = TelegramAccount.objects.get_or_create(user=request.user)
        account = TelegramAccount.objects.select_for_update().get(pk=account.pk)
        if request.method == 'POST':
            if request.POST.get('action') == 'unlink':
                draft = TelegramDraft.objects.filter(user=request.user).first()
                if draft:
                    discard_draft(draft)
                account.telegram_id = None
                account.code_hash = ''
                account.code_expires_at = None
                account.save()
                return redirect('telegram_settings')
            if configured and not account.telegram_id:
                code = secrets.token_urlsafe(24)
                account.code_hash = hashlib.sha256(code.encode()).hexdigest()
                account.code_expires_at = timezone.now() + timedelta(minutes=10)
                account.save()
    response = render(request, 'telegram.html', {
        'account': account, 'code': code, 'configured': configured,
        'bot_username': settings.TELEGRAM_BOT_USERNAME,
    })
    # Preserva a origem dos formulários para o CSRF sem enviar referências a outros sites.
    response['Referrer-Policy'] = 'same-origin'
    return response
