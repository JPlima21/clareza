"""Controla a conversa privada entre o usuário e o bot do Clareza.

Identifica a conta vinculada, recebe documentos e reutiliza a extração e as
validações do site. Gerencia os botões de revisão, a confirmação do cadastro,
as consultas de gastos e a solicitação de anexos, sempre por dono da despesa.
Salva o avanço dos eventos junto às alterações no banco para evitar cadastros
duplicados; as respostas ao Telegram são enviadas depois dessa confirmação.
"""
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from html import escape
import hashlib
import uuid

from django.core.files import File
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .document_extraction import DocumentError, DocumentExtractor, InvalidDocumentError, TemporaryUploadStore
from .forms import ExpenseForm, FilterForm
from .models import Expense, TelegramAccount, TelegramCursor, TelegramDraft
from .telegram_api import TelegramError
from .telegram_link import discard_draft


EDITABLE = {
    'description': 'Descrição', 'beneficiary': 'Beneficiário', 'amount': 'Valor pago',
    'paid_at': 'Data do pagamento', 'category': 'Categoria',
    'transaction_id': 'Identificação da transação', 'notes': 'Observações',
}
HELP = (
    '<b>📎 Ajuda do Clareza</b>\n\n'
    '<b>Cadastrar despesa</b>\nEnvie um PDF, PNG ou JPEG de até 10 MB. '
    'Revise os dados e confirme para salvar.\n'
    '<code>/pendente</code> — revisar cadastro atual\n<code>/cancelar</code> — descartar cadastro\n\n'
    '<b>Consultar gastos</b>\n<code>/resumo</code> — total do mês\n<code>/despesas</code> — histórico\n'
    'Para filtrar por período e categoria:\n<code>/despesas 2026-11-01 2026-11-30 Alimentação</code>\n'
    'Os mesmos filtros funcionam em <code>/resumo</code>. Datas no formato <code>AAAA-MM-DD</code>.\n\n'
    '<b>Sua conta</b>\n<code>/ajuda</code> — instruções\nPara desvincular, use a área Telegram no site.'
)
HELP_ROWS = [[('Resumo do mês', 'menu:resumo'), ('Minhas despesas', 'menu:despesas')],
             [('Revisar pendente', 'menu:pendente')]]


def display_text(value, limit=200):
    text = str(value)
    return escape(text if len(text) <= limit else text[:limit] + '… [texto abreviado]')


def display_amount(value):
    try:
        amount = Decimal(str(value))
        if amount.is_finite():
            return 'R$ ' + format(amount, ',.2f').replace(',', '_').replace('.', ',').replace('_', '.')
    except (InvalidOperation, ValueError):
        pass
    return str(value)


def display_date(value):
    try:
        return date.fromisoformat(str(value)).strftime('%d/%m/%Y')
    except ValueError:
        return str(value)


def keyboard(rows):
    return {'inline_keyboard': [[{'text': text, 'callback_data': data} for text, data in row] for row in rows]}


class TelegramBot:
    def __init__(self, api):
        self.api = api
        self.outgoing = []

    def say(self, chat_id, text, rows=None, html=False):
        payload = {'chat_id': chat_id, 'text': text if html else text[:4000]}
        if html:
            payload['parse_mode'] = 'HTML'
        if rows:
            payload['reply_markup'] = keyboard(rows)
        self.outgoing.append(('sendMessage', payload))

    def process(self, update):
        """Commit business changes and the cursor together, before sending replies.

        A failed reply cannot replay a confirmed expense. Delivery itself is best
        effort: users can repeat queries or /pendente after a network failure.
        """
        self.outgoing = []
        with transaction.atomic():
            cursor, _ = TelegramCursor.objects.get_or_create(pk=1)
            cursor = TelegramCursor.objects.select_for_update().get(pk=1)
            if update['update_id'] < cursor.offset:
                return []
            self.handle(update)
            cursor.offset = update['update_id'] + 1
            cursor.save(update_fields=['offset'])
        return self.outgoing

    def deliver(self, outgoing):
        for method, payload in outgoing:
            if method == 'document':
                expense = Expense.objects.filter(pk=payload['pk'], owner_id=payload['user']).first()
                # Recheck the link in case the user disconnected before delivery.
                if not TelegramAccount.objects.filter(user_id=payload['user'], telegram_id=payload['chat']).exists():
                    continue
                if expense and expense.document:
                    try:
                        self.api.send_document(payload['chat'], expense.document)
                    except (FileNotFoundError, DocumentError):
                        self.api.call('sendMessage', chat_id=payload['chat'], text='Anexo indisponível ou acima de 10 MB.')
            else:
                try:
                    self.api.call(method, **payload)
                except TelegramError as error:
                    # An old callback can no longer be acknowledged, but its
                    # user still needs the resulting status message.
                    if method != 'answerCallbackQuery' or error.status != 400:
                        raise

    def handle(self, update):
        callback = update.get('callback_query')
        message = callback.get('message', {}) if callback else update.get('message', {})
        sender = callback.get('from', {}) if callback else message.get('from', {})
        chat = message.get('chat', {})
        if chat.get('type') != 'private' or chat.get('id') != sender.get('id') or sender.get('is_bot'):
            return
        chat_id = chat['id']
        text = message.get('text', '').strip() if not callback else ''
        if callback:
            self.outgoing.append(('answerCallbackQuery', {'callback_query_id': callback['id']}))
        if text.startswith('/start '):
            return self.link(chat_id, text.split(maxsplit=1)[1])
        account = TelegramAccount.objects.select_for_update().filter(telegram_id=chat_id, user__is_active=True).first()
        if not account:
            return self.say(chat_id, 'Vincule sua conta na área Telegram do Clareza antes de continuar.')
        draft = TelegramDraft.objects.select_for_update().filter(user_id=account.user_id).first()
        if draft and draft.expires_at <= timezone.now():
            discard_draft(draft)
            draft = None
            self.say(chat_id, 'Seu cadastro pendente expirou. Envie o arquivo novamente.')
        if callback:
            return self.callback(account, draft, callback.get('data', ''))
        command, _, arguments = text.partition(' ')
        command = command.split('@')[0]
        if command in ('/start', '/ajuda', '/help'):
            return self.say(chat_id, HELP, HELP_ROWS, html=True)
        if command == '/cancelar':
            if draft:
                discard_draft(draft)
            return self.say(chat_id, 'Cadastro pendente cancelado.' if draft else 'Nenhum cadastro pendente.')
        if command == '/pendente':
            return self.review(account, draft)
        if command in ('/resumo', '/despesas'):
            return self.query(account, arguments, summary=command == '/resumo')
        document = message.get('document')
        if not document and message.get('photo'):
            document = {**message['photo'][-1], 'file_name': 'foto.jpg'}
        if document:
            if draft:
                return self.say(chat_id, 'Existe um cadastro pendente. Deseja descartá-lo? Depois envie o novo arquivo.', [
                    [('Descartar pendente', f'draft:{draft.pk}:cancel')],
                    [('Continuar revisão', f'draft:{draft.pk}:review')],
                ])
            return self.receive(account, document)
        if draft and draft.editing and text:
            return self.edit_value(account, draft, text)
        self.say(chat_id, 'Use /ajuda para ver os comandos ou envie um comprovante.')

    def link(self, chat_id, code):
        digest = hashlib.sha256(code.encode()).hexdigest()
        account = TelegramAccount.objects.select_for_update().filter(
            code_hash=digest, code_expires_at__gt=timezone.now(), user__is_active=True,
        ).first()
        if not account or account.telegram_id or TelegramAccount.objects.filter(telegram_id=chat_id).exists():
            return self.say(chat_id, 'Código inválido, expirado ou conta já vinculada. Confira a área Telegram no site.')
        account.telegram_id = chat_id
        account.code_hash = ''
        account.code_expires_at = None
        account.save()
        self.say(chat_id, '<b>✅ Conta vinculada!</b>\nSeus documentos passam pelo Telegram; '
                 'a extração é feita no Clareza.\n\n' + HELP, HELP_ROWS, html=True)

    def receive(self, account, document):
        store = TemporaryUploadStore()
        token = None
        try:
            upload = self.api.download(document)
            token = store.create(upload, account.user_id)
            path = store.resolve(token, account.user_id)
            try:
                result = DocumentExtractor().extract(path)
                fields, warnings = result.fields, result.warnings
            except InvalidDocumentError:
                raise
            except DocumentError as error:
                fields, warnings = {}, [str(error)]
            draft = TelegramDraft.objects.create(
                user_id=account.user_id, token=token, fields=fields, warnings=warnings,
                expires_at=timezone.now() + timedelta(hours=1),
            )
        except DocumentError as error:
            if token:
                try:
                    store.delete(token, account.user_id)
                except DocumentError:
                    pass
            return self.say(account.telegram_id, str(error))
        self.review(account, draft)

    def review(self, account, draft, errors=None):
        if not draft:
            return self.say(account.telegram_id, 'Nenhum cadastro pendente. Envie um comprovante.')
        def value(name):
            raw = draft.fields.get(name)
            if not raw:
                return '<b>⚠ Obrigatório: não informado</b>' if ExpenseForm.base_fields[name].required else 'Não informado'
            if name == 'amount':
                raw = display_amount(raw)
            elif name == 'paid_at':
                raw = display_date(raw)
            return display_text(raw, 350 if name == 'notes' else 200)

        text = '<b>📎 Revisão do comprovante</b>\nConfira os dados antes de salvar. Expira em até uma hora.'
        if errors:
            text += '\n\n<b>⚠ Corrija os dados antes de salvar</b>\n' + '\n'.join(
                f'• <b>{display_text(EDITABLE.get(key, "Arquivo"))}:</b> {display_text(" ".join(values), 180)}'
                for key, values in errors.items()
            )
        amount = value('amount')
        if draft.fields.get('amount'):
            amount = f'<b>{amount}</b>'
        text += f'\n\n<b>Pagamento</b>\nValor: {amount}\nData: {value("paid_at")}'
        text += f'\n\n<b>Identificação</b>\nDescrição: {value("description")}\nCategoria: {value("category")}\nBeneficiário: {value("beneficiary")}'
        if draft.fields.get('transaction_id'):
            text += f'\nID transação: {value("transaction_id")}'
        if draft.fields.get('notes'):
            text += f'\n\n<b>Observações</b>\n{value("notes")}'
        if draft.warnings:
            text += '\n\n<b>⚠ Avisos</b>\n' + display_text('\n'.join('• ' + str(warning) for warning in draft.warnings), 500)
        prefix = f'draft:{draft.pk}:'
        buttons = [(label, prefix + name) for name, label in (
            ('description', 'Descrição'), ('category', 'Categoria'), ('amount', 'Valor'),
            ('paid_at', 'Data'), ('beneficiary', 'Beneficiário'), ('transaction_id', 'ID transação'),
            ('notes', 'Observações'),
        )]
        rows = [buttons[index:index + 2] for index in range(0, len(buttons), 2)]
        rows.append([('Confirmar e salvar', prefix + 'confirm'), ('Cancelar', prefix + 'cancel')])
        self.say(account.telegram_id, text, rows, html=True)

    def callback(self, account, draft, data):
        if data == 'menu:pendente':
            return self.review(account, draft)
        if data in ('menu:resumo', 'menu:despesas'):
            return self.query(account, summary=data == 'menu:resumo')
        parts = data.split(':')
        if len(parts) == 3 and parts[0] == 'draft':
            if not draft or str(draft.pk) != parts[1]:
                return self.say(account.telegram_id, 'Este cadastro já foi encerrado ou expirou. Use /pendente.')
            action = parts[2]
            if action == 'cancel':
                discard_draft(draft)
                return self.say(account.telegram_id, 'Cadastro descartado. Você pode enviar outro arquivo.')
            if action == 'confirm':
                return self.confirm(account, draft)
            if action in EDITABLE:
                draft.editing = action
                draft.save(update_fields=['editing'])
                if action == 'category':
                    return self.say(account.telegram_id, 'Escolha a categoria:', [
                        [(label, f'cat:{draft.pk}:{index}')] for index, (_, label) in enumerate(Expense.CATEGORIES)
                    ])
                hint = ' Use AAAA-MM-DD.' if action == 'paid_at' else ' Use ponto decimal, por exemplo 123.45.' if action == 'amount' else ''
                return self.say(account.telegram_id, f'<b>Editar {escape(EDITABLE[action])}</b>\n\nEnvie o novo valor.{hint}\nPara limpar um campo opcional, envie <code>-</code>.', html=True)
            if action == 'review':
                return self.review(account, draft)
        if len(parts) == 3 and parts[0] == 'cat' and draft and str(draft.pk) == parts[1]:
            if parts[2].isdigit() and 0 <= int(parts[2]) < len(Expense.CATEGORIES):
                return self.edit_value(account, draft, Expense.CATEGORIES[int(parts[2])][0], field='category')
        if len(parts) == 3 and parts[0] == 'expense' and parts[1].isdigit():
            expense = Expense.objects.filter(pk=int(parts[1]), owner_id=account.user_id).first()
            if not expense:
                return self.say(account.telegram_id, 'Despesa não encontrada.')
            if parts[2] == 'file':
                if not expense.document:
                    return self.say(account.telegram_id, 'Esta despesa não possui comprovante.')
                self.outgoing.append(('document', {'pk': expense.pk, 'user': account.user_id, 'chat': account.telegram_id}))
                return
            if parts[2] == 'view':
                return self.say(account.telegram_id, (
                    f'<b>{display_text(expense.description)}</b>\n\n'
                    f'<b>{display_text(display_amount(expense.amount))}</b>\nPagamento: {display_date(expense.paid_at)}\n'
                    f'Categoria: {display_text(expense.category)}\nBeneficiário: {display_text(expense.beneficiary or "Não informado")}'
                ), [[('Receber comprovante', f'expense:{expense.pk}:file')]], html=True)
        # Pagination carries validated filter values instead of a shared session.
        if len(parts) == 3 and parts[0] == 'filter':
            start, end = parts[1:]
            filters = FilterForm({'start': start, 'end': end})
            if filters.is_valid() and filters.cleaned_data.get('start') and filters.cleaned_data.get('end'):
                return self.say(account.telegram_id, 'Selecione a categoria:', [
                    [(label, f'page:1:{start}:{end}:{index}')] for index, (_, label) in enumerate(Expense.CATEGORIES)
                ] + [[('Todas', f'page:1:{start}:{end}:-')]])
        if data.startswith('page:'):
            try:
                _, page, start, end, category = data.split(':')
                if category != '-' and (not category.isdigit() or not 0 <= int(category) < len(Expense.CATEGORIES)):
                    raise ValueError
                category = Expense.CATEGORIES[int(category)][0] if category != '-' else ''
                if int(page) < 1:
                    raise ValueError
            except (ValueError, IndexError):
                return self.say(account.telegram_id, 'Filtro inválido. Use /despesas.')
            return self.query(account, f'{start} {end} {category}', page=int(page))
        self.say(account.telegram_id, 'Botão inválido ou expirado. Use /ajuda.')

    def edit_value(self, account, draft, text, field=None):
        field = field or draft.editing
        if field not in EDITABLE:
            return self.review(account, draft)
        value = '' if text == '-' else text
        form_field = ExpenseForm.base_fields[field]
        if field == 'amount':
            value = value.replace(',', '.')
        try:
            cleaned = form_field.clean(value)
            Expense._meta.get_field(field).clean(cleaned, None)
        except ValidationError as error:
            return self.say(account.telegram_id, f'<b>⚠ Confira {escape(EDITABLE[field])}</b>\n\n{display_text(" ".join(error.messages), 500)}\nEnvie o valor novamente.', html=True)
        draft.fields = {**draft.fields, field: str(cleaned) if cleaned is not None else ''}
        draft.editing = ''
        draft.save(update_fields=['fields', 'editing'])
        self.review(account, draft)

    def confirm(self, account, draft):
        form = ExpenseForm({**draft.fields, 'extraction_token': draft.token}, user=account.user)
        if not form.is_valid():
            return self.review(account, draft, errors=form.errors)
        path = TemporaryUploadStore().resolve(draft.token, account.user_id)
        expense = form.save(commit=False)
        expense.owner_id = account.user_id
        try:
            with path.open('rb') as source:
                expense.document.save(f'{uuid.uuid4().hex}{path.suffix}', File(source), save=False)
            expense.save()
        except Exception:
            if expense.document:
                expense.document.delete(save=False)
            raise
        draft.delete()
        transaction.on_commit(lambda: path.unlink(missing_ok=True))
        self.say(account.telegram_id, f'<b>✅ Despesa #{expense.pk} salva</b>\n\n{display_text(expense.description)}\n<b>{display_text(display_amount(expense.amount))}</b> · {display_date(expense.paid_at)}', html=True)

    def query(self, account, arguments='', summary=False, page=1):
        today = timezone.localdate()
        values = arguments.split(maxsplit=2)
        if values and len(values) < 2:
            return self.say(account.telegram_id, 'Informe início e fim: /despesas AAAA-MM-DD AAAA-MM-DD [categoria]')
        start, end = values[:2] if values else (today.replace(day=1).isoformat(), today.isoformat())
        category = values[2] if len(values) == 3 else ''
        filters = FilterForm({'start': start, 'end': end, 'category': category})
        if not filters.is_valid():
            return self.say(account.telegram_id, 'Período ou categoria inválidos. Use /ajuda.')
        data = filters.cleaned_data
        qs = Expense.objects.filter(owner_id=account.user_id, paid_at__gte=data['start'], paid_at__lte=data['end'])
        if category:
            qs = qs.filter(category=category)
        if summary:
            total = qs.aggregate(total=Sum('amount'))['total'] or 0
            groups = qs.values('category').annotate(total=Sum('amount')).order_by('category')
            return self.say(account.telegram_id, f'<b>Resumo de gastos</b>\n{display_date(start)} a {display_date(end)}\n{qs.count()} despesas\n\n<b>Total: {display_amount(total)}</b>\n' + '\n'.join(
                f'• {display_text(item["category"])}: {display_amount(item["total"])}' for item in groups
            ), html=True)
        result = Paginator(qs, 5).get_page(page)
        category_index = next((str(i) for i, (key, _) in enumerate(Expense.CATEGORIES) if key == category), '-')
        rows = [[(f'#{expense.pk} {expense.description[:35]} — {display_amount(expense.amount)}', f'expense:{expense.pk}:view')] for expense in result]
        navigation = []
        for label, number in [('Anterior', result.number - 1), ('Próxima', result.number + 1)]:
            if 1 <= number <= result.paginator.num_pages:
                navigation.append((label, f'page:{number}:{start}:{end}:{category_index}'))
        if navigation:
            rows.append(navigation)
        rows.append([('Filtrar categoria', f'filter:{start}:{end}')])
        self.say(account.telegram_id, f'Despesas de {display_date(start)} a {display_date(end)}. Página {result.number}/{result.paginator.num_pages}.\n' + (
            'Selecione uma despesa para ver detalhes.' if result.paginator.count else 'Nenhuma despesa neste período.'
        ), rows)
