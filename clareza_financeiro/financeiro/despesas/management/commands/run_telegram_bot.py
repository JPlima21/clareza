"""Disponibiliza o comando Django `python manage.py run_telegram_bot`.

Confere o token e a identidade do bot, verifica se existe webhook e registra
o menu de comandos. Busca eventos continuamente com getUpdates (long polling),
encaminha-os para TelegramBot e envia as respostas. Também limpa pendentes
expirados e trata falhas de comunicação. Deve rodar em uma única instância,
depois que o serviço web terminar as migrações do banco.
"""
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from despesas.document_extraction import TemporaryUploadStore
from despesas.models import TelegramCursor, TelegramDraft
from despesas.telegram_api import TelegramAPI, TelegramError
from despesas.telegram_bot import TelegramBot
from despesas.telegram_link import discard_draft
from django.utils import timezone


class Command(BaseCommand):
    help = 'Recebe mensagens do Telegram por long polling. Execute somente uma instância.'

    def handle(self, *args, **options):
        if not settings.TELEGRAM_BOT_TOKEN:
            raise CommandError('Defina TELEGRAM_BOT_TOKEN no ambiente.')
        api = TelegramAPI(settings.TELEGRAM_BOT_TOKEN)
        try:
            identity = api.call('getMe')
            if identity.get('username', '').lower() != settings.TELEGRAM_BOT_USERNAME.lower():
                raise CommandError('TELEGRAM_BOT_USERNAME não corresponde ao token configurado.')
            if api.call('getWebhookInfo').get('url'):
                raise CommandError('O bot possui webhook ativo. Remova-o antes de usar long polling; consulte o guia.')
            api.call('setMyCommands', commands=[
                {'command': name, 'description': description} for name, description in [
                    ('ajuda', 'Como usar o Clareza'), ('resumo', 'Total e categorias do mês'),
                    ('despesas', 'Consultar despesas e comprovantes'), ('pendente', 'Revisar cadastro pendente'),
                    ('cancelar', 'Descartar cadastro pendente'),
                ]
            ])
        except TelegramError as error:
            raise CommandError(str(error)) from None
        bot = TelegramBot(api)
        self.stdout.write('Bot iniciado por long polling. Interrompa com Ctrl+C.')
        cleanup_at = 0
        try:
            while True:
                close_old_connections()
                if time.monotonic() >= cleanup_at:
                    for draft in TelegramDraft.objects.filter(expires_at__lte=timezone.now()):
                        discard_draft(draft)
                    TemporaryUploadStore().cleanup()
                    cleanup_at = time.monotonic() + 60
                cursor, _ = TelegramCursor.objects.get_or_create(pk=1)
                try:
                    updates = api.call('getUpdates', offset=cursor.offset, timeout=30, allowed_updates=['message', 'callback_query'])
                    for update in updates:
                        outgoing = bot.process(update)
                        try:
                            bot.deliver(outgoing)
                        except TelegramError as error:
                            # Financial changes have committed. Never replay them for a reply failure.
                            self.stderr.write(f'Resposta não entregue: código {error.status}. Consulte /pendente ou /despesas.')
                            if error.status == 429:
                                time.sleep(error.retry_after)
                except TelegramError as error:
                    if error.status in (401, 404, 409):
                        raise CommandError(f'Polling interrompido: código {error.status}. Confira token e instâncias em execução.') from None
                    self.stderr.write(f'Comunicação interrompida: código {error.status}; nova tentativa em {error.retry_after}s.')
                    time.sleep(error.retry_after)
        except KeyboardInterrupt:
            self.stdout.write('Bot encerrado.')
