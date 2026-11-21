"""Faz a comunicação HTTPS com a API de bots do Telegram.

Envia requisições e interpreta respostas JSON, baixa comprovantes com limite
de tamanho e transmite anexos usando multipart. Converte falhas de comunicação
em erros que não exibem o token nem os endereços que contêm essa credencial.
As decisões sobre cadastro e acesso às despesas ficam em telegram_bot.py.
"""
import json
import mimetypes
import secrets
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from django.core.files.uploadedfile import SimpleUploadedFile

from .document_extraction import MAX_FILE_SIZE, DocumentError


class TelegramError(Exception):
    def __init__(self, status=0, retry_after=5):
        self.status = status
        self.retry_after = min(max(int(retry_after), 1), 60)
        super().__init__(f'Telegram indisponível (código {status}).')


class TelegramAPI:
    def __init__(self, token):
        self.token = token

    def call(self, method, **payload):
        request = Request(
            f'https://api.telegram.org/bot{self.token}/{method}',
            data=json.dumps(payload).encode(),
            headers={'Content-Type': 'application/json'},
        )
        return self._request(request)

    def _request(self, request):
        try:
            with urlopen(request, timeout=40) as response:
                data = json.load(response)
        except HTTPError as error:
            delay = 5
            try:
                delay = json.load(error).get('parameters', {}).get('retry_after', 5)
            except (ValueError, OSError):
                pass
            raise TelegramError(error.code, delay) from None
        except (URLError, OSError, ValueError):
            raise TelegramError() from None
        if not data.get('ok'):
            raise TelegramError(data.get('error_code', 0), data.get('parameters', {}).get('retry_after', 5))
        return data['result']

    def download(self, document):
        if document.get('file_size', 0) > MAX_FILE_SIZE:
            raise DocumentError('O arquivo deve ter até 10 MB.')
        info = self.call('getFile', file_id=document['file_id'])
        if info.get('file_size', 0) > MAX_FILE_SIZE:
            raise DocumentError('O arquivo deve ter até 10 MB.')
        path = quote(info['file_path'], safe='/')
        try:
            with urlopen(f'https://api.telegram.org/file/bot{self.token}/{path}', timeout=40) as response:
                content = response.read(MAX_FILE_SIZE + 1)
        except (URLError, OSError):
            raise TelegramError() from None
        if len(content) > MAX_FILE_SIZE:
            raise DocumentError('O arquivo deve ter até 10 MB.')
        return SimpleUploadedFile(document.get('file_name', 'imagem.jpg'), content)

    def send_document(self, chat_id, document):
        # A bounded multipart upload avoids adding a separate HTTP dependency.
        with document.open('rb') as source:
            content = source.read(MAX_FILE_SIZE + 1)
        if len(content) > MAX_FILE_SIZE:
            raise DocumentError('O anexo excede o limite de 10 MB.')
        from pathlib import Path
        filename = 'comprovante' + Path(document.name).suffix.lower()
        boundary = secrets.token_hex(24)
        mime = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
        body = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="document"; filename="{filename}"\r\n'
            f'Content-Type: {mime}\r\n\r\n'
        ).encode() + content + f'\r\n--{boundary}--\r\n'.encode()
        return self._request(Request(
            f'https://api.telegram.org/bot{self.token}/sendDocument', data=body,
            headers={'Content-Type': f'multipart/form-data; boundary={boundary}'},
        ))
