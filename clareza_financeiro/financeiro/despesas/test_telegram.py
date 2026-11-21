"""Verifica a integração com documentos sintéticos e chamadas de API simuladas.

Cobre vínculo, expiração, CSRF, isolamento entre usuários, revisão e confirmação
de despesas, consultas, anexos e retomada do polling. Verifica também que eventos
repetidos ou falhas nas respostas não recriem uma despesa já confirmada.
Não precisa de token real e não envia documentos ao Telegram.
"""
from datetime import timedelta
from decimal import Decimal
from io import BytesIO, StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, Mock, patch
from urllib.error import HTTPError, URLError

from django.contrib.auth import get_user_model
from django.core.management import call_command, CommandError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, SimpleTestCase, override_settings
from django.utils import timezone
from PIL import Image

from .document_extraction import DocumentError, ExtractionResult, TemporaryUploadStore, UnreadableDocumentError
from .models import Expense, TelegramAccount, TelegramCursor, TelegramDraft
from .telegram_api import TelegramAPI, TelegramError
from .telegram_bot import TelegramBot


def image_upload():
    stream = BytesIO()
    Image.new('RGB', (10, 10), 'white').save(stream, 'PNG')
    return SimpleUploadedFile('comprovante.png', stream.getvalue())


@override_settings(TELEGRAM_BOT_TOKEN='synthetic-token', TELEGRAM_BOT_USERNAME='clareza_test_bot')
class TelegramFlowTest(TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.override = self.settings(
            MEDIA_ROOT=Path(self.directory.name) / 'media',
            TEMP_UPLOAD_ROOT=Path(self.directory.name) / 'temporary',
        )
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.user = get_user_model().objects.create_user('telegram-owner')
        self.other = get_user_model().objects.create_user('telegram-other')
        self.account = TelegramAccount.objects.create(user=self.user, telegram_id=123)
        self.api = Mock()
        self.bot = TelegramBot(self.api)
        self.sequence = 0

    def message(self, text='', sender=123, document=None, callback=None, group=False):
        self.sequence += 1
        message = {'chat': {'id': sender, 'type': 'group' if group else 'private'}, 'from': {'id': sender}, 'text': text}
        if document:
            message['document'] = document
        update = {'update_id': self.sequence, 'message': message}
        if callback:
            update = {'update_id': self.sequence, 'callback_query': {
                'id': 'callback-test', 'from': {'id': sender}, 'message': message, 'data': callback,
            }}
        with self.captureOnCommitCallbacks(execute=True):
            return self.bot.process(update)

    def draft(self):
        token = TemporaryUploadStore().create(image_upload(), self.user.pk)
        return TelegramDraft.objects.create(user=self.user, token=token, fields={
            'description': 'Internet', 'amount': '99.90', 'paid_at': '2026-11-19', 'category': 'Serviços',
        }, expires_at=timezone.now() + timedelta(hours=1))

    def test_link_code_single_use_expiry_and_unlink(self):
        self.account.telegram_id = None
        self.account.save()
        self.assertEqual(self.client.get('/telegram/').status_code, 302)
        self.client.force_login(self.user)
        response = self.client.post('/telegram/', {'action': 'generate'})
        code = response.context['code']
        self.assertEqual(response['Referrer-Policy'], 'same-origin')
        self.assertIn('no-store', response['Cache-Control'])
        self.account.refresh_from_db()
        self.assertNotEqual(self.account.code_hash, code)
        self.message('/start ' + code)
        self.account.refresh_from_db()
        self.assertEqual(self.account.telegram_id, 123)
        self.assertEqual(self.account.code_hash, '')
        self.message('/start ' + code, sender=456)
        self.assertFalse(TelegramAccount.objects.filter(telegram_id=456).exists())
        draft = self.draft()
        path = TemporaryUploadStore().resolve(draft.token, self.user.pk)
        self.client.post('/telegram/', {'action': 'unlink'})
        self.assertFalse(path.exists())
        self.assertFalse(TelegramDraft.objects.exists())
        self.assertFalse(TelegramAccount.objects.exclude(telegram_id=None).exists())
        response = self.client.post('/telegram/', {'action': 'generate'})
        self.account.refresh_from_db()
        self.account.code_expires_at = timezone.now() - timedelta(seconds=1)
        self.account.save()
        self.message('/start ' + response.context['code'])
        self.account.refresh_from_db()
        self.assertIsNone(self.account.telegram_id)

    def test_link_post_requires_csrf_and_active_user(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post('/telegram/', {'action': 'generate'}).status_code, 403)
        self.user.is_active = False
        self.user.save()
        self.assertIn('Vincule', str(self.message('/resumo')))

    def test_link_forms_accept_same_origin_with_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        response = client.get('/telegram/', HTTP_HOST='localhost:8000')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Referrer-Policy'], 'same-origin')
        token = client.cookies['csrftoken'].value
        self.account.telegram_id = None
        self.account.save()
        response = client.post('/telegram/', {
            'action': 'generate', 'csrfmiddlewaretoken': token,
        }, HTTP_ORIGIN='http://localhost:8000', HTTP_HOST='localhost:8000')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['code'])
        self.account.refresh_from_db()
        self.assertTrue(self.account.code_hash)
        self.account.telegram_id = 123
        self.account.save()
        response = client.post('/telegram/', {
            'action': 'unlink', 'csrfmiddlewaretoken': token,
        }, HTTP_ORIGIN='http://localhost:8000', HTTP_HOST='localhost:8000')
        self.assertRedirects(response, '/telegram/', fetch_redirect_response=False)
        self.account.refresh_from_db()
        self.assertIsNone(self.account.telegram_id)
        self.assertEqual(self.account.code_hash, '')
        self.assertIsNone(self.account.code_expires_at)

    def test_link_forms_reject_untrusted_origins_with_valid_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        client.get('/telegram/', HTTP_HOST='localhost:8000')
        token = client.cookies['csrftoken'].value
        for origin in ('null', 'https://foreign.example'):
            for action in ('generate', 'unlink'):
                with self.subTest(origin=origin, action=action):
                    self.account.telegram_id = None if action == 'generate' else 123
                    self.account.code_hash = 'existing-hash'
                    self.account.code_expires_at = timezone.now() + timedelta(minutes=10)
                    self.account.save()
                    expected = (
                        self.account.telegram_id, self.account.code_hash,
                        self.account.code_expires_at,
                    )
                    response = client.post('/telegram/', {
                        'action': action, 'csrfmiddlewaretoken': token,
                    }, HTTP_ORIGIN=origin, HTTP_HOST='localhost:8000')
                    self.assertEqual(response.status_code, 403)
                    self.account.refresh_from_db()
                    self.assertEqual((
                        self.account.telegram_id, self.account.code_hash,
                        self.account.code_expires_at,
                    ), expected)

    def test_receive_review_correct_confirm_and_replay(self):
        self.api.download.return_value = image_upload()
        with patch('despesas.telegram_bot.DocumentExtractor.extract', return_value=ExtractionResult(
            fields={'description': 'Internet', 'amount': '99.90', 'paid_at': '2026-11-19'},
            warnings=['Revise a categoria.'],
        )):
            outgoing = self.message(document={'file_id': 'fake'})
        self.assertIn('Revise a categoria', str(outgoing))
        self.assertEqual(Expense.objects.count(), 0)
        draft = TelegramDraft.objects.get()
        self.message(callback=f'draft:{draft.pk}:confirm')
        self.assertEqual(Expense.objects.count(), 0)
        self.message(callback=f'draft:{draft.pk}:category')
        category_index = next(i for i, (key, _) in enumerate(Expense.CATEGORIES) if key == 'Serviços')
        self.message(callback=f'cat:{draft.pk}:{category_index}')
        self.message(callback=f'draft:{draft.pk}:amount')
        self.message('-1')
        draft.refresh_from_db()
        self.assertEqual(draft.fields['amount'], '99.90')
        self.message('100,50')
        self.message(callback=f'draft:{draft.pk}:confirm')
        expense = Expense.objects.get()
        self.assertEqual(expense.amount, Decimal('100.50'))
        self.assertEqual(expense.owner, self.user)
        self.assertTrue(Path(expense.document.path).exists())
        self.assertFalse(TelegramDraft.objects.exists())
        self.assertEqual(list(Path(self.directory.name).glob('temporary/*/*')), [])
        self.message(callback=f'draft:{draft.pk}:confirm')
        replay = {'update_id': self.sequence, 'message': {'chat': {'id': 123, 'type': 'private'}, 'from': {'id': 123}, 'text': '/resumo'}}
        self.assertEqual(TelegramBot(self.api).process(replay), [])
        self.assertEqual(Expense.objects.count(), 1)
        self.assertEqual(TelegramCursor.objects.get(pk=1).offset, self.sequence + 1)

    def test_invalid_document_ocr_failure_replacement_cancel_and_expiry(self):
        self.api.download.return_value = SimpleUploadedFile('bad.pdf', b'not-pdf')
        self.message(document={'file_id': 'invalid'})
        self.assertFalse(TelegramDraft.objects.exists())
        self.api.download.return_value = image_upload()
        with patch('despesas.telegram_bot.DocumentExtractor.extract', side_effect=UnreadableDocumentError('Preencha manualmente.')):
            outgoing = self.message(document={'file_id': 'unreadable'})
        self.assertIn('Preencha manualmente', str(outgoing))
        draft = TelegramDraft.objects.get()
        token = draft.token
        calls = self.api.download.call_count
        self.message(document={'file_id': 'replacement'})
        self.assertEqual(self.api.download.call_count, calls)
        self.assertEqual(TelegramDraft.objects.get().token, token)
        self.message(callback=f'draft:{draft.pk}:cancel')
        self.assertFalse(TelegramDraft.objects.exists())
        self.assertEqual(list(Path(self.directory.name).glob('temporary/*/*')), [])
        draft = self.draft()
        draft.expires_at = timezone.now() - timedelta(seconds=1)
        draft.save()
        self.message(callback=f'draft:{draft.pk}:confirm')
        self.assertFalse(TelegramDraft.objects.exists())
        self.assertFalse(Expense.objects.exists())

    def test_isolation_filters_pagination_and_document_delivery(self):
        hidden = Expense.objects.create(owner=self.other, description='Privada', amount='500', paid_at='2026-11-19', category='Outros', document=image_upload())
        for i in range(7):
            Expense.objects.create(owner=self.user, description=f'Internet {i}', amount='10', paid_at='2026-11-19', category='Serviços', document=image_upload())
        self.assertIn('70,00', str(self.message('/resumo 2026-11-01 2026-11-30')))
        self.assertNotIn('Privada', str(self.message('/despesas 2026-11-01 2026-11-30')))
        self.assertIn('2/2', str(self.message(callback='page:2:2026-11-01:2026-11-30:-')))
        self.assertIn('Selecione a categoria', str(self.message(callback='filter:2026-11-01:2026-11-30')))
        self.assertIn('Nenhuma despesa', str(self.message('/despesas 2026-11-01 2026-11-30 Outros')))
        self.assertIn('inválidos', str(self.message('/despesas 2026-12-01 2026-11-01')))
        self.assertIn('não encontrada', str(self.message(callback=f'expense:{hidden.pk}:file')))
        self.assertEqual(self.message('/resumo', group=True), [])
        self.assertIn('Vincule', str(self.message('/resumo', sender=456)))
        expense = Expense.objects.filter(owner=self.user).first()
        outgoing = self.message(callback=f'expense:{expense.pk}:file')
        self.bot.deliver(outgoing)
        self.api.send_document.assert_called_once()
        self.api.reset_mock()
        self.account.telegram_id = None
        self.account.save()
        self.bot.deliver(outgoing)
        self.api.send_document.assert_not_called()

    def test_download_failure_does_not_advance_cursor(self):
        self.message('/ajuda')
        offset = TelegramCursor.objects.get(pk=1).offset
        self.api.download.side_effect = TelegramError()
        with self.assertRaises(TelegramError):
            self.message(document={'file_id': 'retry'})
        self.assertEqual(TelegramCursor.objects.get(pk=1).offset, offset)
        self.assertFalse(TelegramDraft.objects.exists())

    def test_review_html_escaping_layout_and_brazilian_formats(self):
        draft = self.draft()
        draft.fields.update({name: '<>&' for name in ('description', 'beneficiary', 'transaction_id', 'notes')})
        draft.fields['amount'] = '1234.56'
        draft.warnings = ['Atenção <>&']
        draft.save()
        payload = self.message('/pendente')[0][1]
        self.assertEqual(payload['parse_mode'], 'HTML')
        self.assertEqual(payload['text'].count('&lt;&gt;&amp;'), 5)
        self.assertIn('R$ 1.234,56', payload['text'])
        self.assertIn('19/11/2026', payload['text'])
        rows = payload['reply_markup']['inline_keyboard']
        self.assertEqual([len(row) for row in rows], [2, 2, 2, 1, 2])
        self.assertEqual([button['callback_data'] for row in rows for button in row], [
            f'draft:{draft.pk}:{name}' for name in (
                'description', 'category', 'amount', 'paid_at', 'beneficiary',
                'transaction_id', 'notes', 'confirm', 'cancel',
            )
        ])

    def test_long_review_keeps_complete_html_and_reports_truncation(self):
        draft = self.draft()
        draft.fields = {name: '<>&' * 3000 for name in draft.fields}
        draft.fields['notes'] = '<>&' * 3000
        draft.warnings = ['<>&' * 3000] * 10
        draft.save()
        text = self.message('/pendente')[0][1]['text']
        from html import unescape
        from xml.etree import ElementTree
        ElementTree.fromstring('<root>' + text + '</root>')
        self.assertLess(len(unescape(text)), 4096)
        self.assertIn('[texto abreviado]', text)

    def test_confirmation_errors_send_one_review_with_buttons(self):
        draft = self.draft()
        draft.fields = {}
        draft.save()
        outgoing = self.message(callback=f'draft:{draft.pk}:confirm')
        messages = [payload for method, payload in outgoing if method == 'sendMessage']
        self.assertEqual(len(messages), 1)
        self.assertIn('Corrija os dados', messages[0]['text'])
        self.assertIn('Obrigatório: não informado', messages[0]['text'])
        self.assertIn('reply_markup', messages[0])
        self.assertFalse(Expense.objects.exists())

    def test_help_shortcuts_and_expense_detail_escape_dynamic_text(self):
        help_payload = self.message('/ajuda')[0][1]
        self.assertEqual(help_payload['parse_mode'], 'HTML')
        self.assertIn('menu:resumo', str(help_payload['reply_markup']))
        self.assertIn('Nenhum cadastro pendente', str(self.message(callback='menu:pendente')))
        self.assertIn('Resumo de gastos', str(self.message(callback='menu:resumo')))
        self.assertIn('Página', str(self.message(callback='menu:despesas')))
        expense = Expense.objects.create(owner=self.user, description='<>&', beneficiary='<>&',
                                         amount='1234.56', paid_at='2026-11-19', category='Outros')
        detail = self.message(callback=f'expense:{expense.pk}:view')[-1][1]
        self.assertEqual(detail['text'].count('&lt;&gt;&amp;'), 2)
        self.assertIn('R$ 1.234,56', detail['text'])
        self.assertIn('19/11/2026', detail['text'])
        listing = self.message('/despesas 2026-11-01 2026-11-30')[0][1]
        self.assertIn('<>&', listing['reply_markup']['inline_keyboard'][0][0]['text'])
        self.assertNotIn('parse_mode', listing)

    def test_response_failure_cannot_recreate_expense(self):
        draft = self.draft()
        outgoing = self.message(callback=f'draft:{draft.pk}:confirm')
        self.api.call.side_effect = TelegramError(403)
        with self.assertRaises(TelegramError):
            self.bot.deliver(outgoing)
        self.message(callback=f'draft:{draft.pk}:confirm')
        self.assertEqual(Expense.objects.count(), 1)

    def test_stale_callback_acknowledgement_does_not_hide_status(self):
        self.api.call.side_effect = [TelegramError(400), True]
        outgoing = self.message(callback='draft:999:confirm')
        self.bot.deliver(outgoing)
        self.assertEqual(self.api.call.call_count, 2)

    def test_other_user_cannot_confirm_draft_or_read_attachment(self):
        draft = self.draft()
        TelegramAccount.objects.create(user=self.other, telegram_id=456)
        self.message(callback=f'draft:{draft.pk}:confirm', sender=456)
        self.assertFalse(Expense.objects.exists())
        self.assertTrue(TelegramDraft.objects.filter(pk=draft.pk).exists())

    def test_worker_startup_and_restart_cursor(self):
        TelegramCursor.objects.create(pk=1, offset=250)
        with patch('despesas.management.commands.run_telegram_bot.TelegramAPI') as factory:
            api = factory.return_value
            api.call.side_effect = [{'username': 'clareza_test_bot'}, {'url': ''}, True, KeyboardInterrupt()]
            call_command('run_telegram_bot', stdout=StringIO())
            self.assertEqual(api.call.call_args.args[0], 'getUpdates')
            self.assertEqual(api.call.call_args.kwargs['offset'], 250)
            api.call.side_effect = [{'username': 'clareza_test_bot'}, {'url': 'https://example.com/webhook'}]
            with self.assertRaisesMessage(CommandError, 'webhook ativo'):
                call_command('run_telegram_bot')
            api.call.side_effect = [{'username': 'wrong_bot'}]
            with self.assertRaisesMessage(CommandError, 'não corresponde'):
                call_command('run_telegram_bot')
        with self.settings(TELEGRAM_BOT_TOKEN=''):
            with self.assertRaisesMessage(CommandError, 'Defina TELEGRAM_BOT_TOKEN'):
                call_command('run_telegram_bot')


class TelegramAPITest(SimpleTestCase):
    def test_json_request_and_safe_error_messages(self):
        api = TelegramAPI('do-not-expose-token')
        with patch('despesas.telegram_api.urlopen') as opening:
            response = opening.return_value.__enter__.return_value
            response.read.return_value = b'{"ok":true,"result":{"id":1}}'
            self.assertEqual(api.call('getMe'), {'id': 1})
            self.assertEqual(opening.call_args.kwargs['timeout'], 40)
        for error in [URLError('https://secret-token'), HTTPError('https://secret-token', 429, 'limit', {}, BytesIO(b'{"parameters":{"retry_after":9}}'))]:
            with patch('despesas.telegram_api.urlopen', side_effect=error):
                with self.assertRaises(TelegramError) as raised:
                    api.call('getMe')
                self.assertNotIn('token', str(raised.exception))

    def test_download_bounds_and_multipart(self):
        api = TelegramAPI('synthetic')
        with self.assertRaises(DocumentError):
            api.download({'file_size': 11 * 1024 * 1024})
        with patch.object(api, 'call', return_value={'file_path': 'documents/test.png'}), patch('despesas.telegram_api.urlopen') as opening:
            opening.return_value.__enter__.return_value.read.return_value = b'file-content'
            upload = api.download({'file_id': 'id', 'file_name': 'test.png'})
            self.assertEqual(upload.name, 'test.png')
            self.assertEqual(upload.read(), b'file-content')
        document = MagicMock(name='document')
        document.name = 'private/random.png'
        document.open.return_value.__enter__.return_value.read.return_value = b'png-content'
        with patch.object(api, '_request', return_value=True) as request:
            api.send_document(123, document)
            body = request.call_args.args[0].data
            self.assertIn(b'name="chat_id"', body)
            self.assertIn(b'filename="comprovante.png"', body)
            self.assertIn(b'png-content', body)
