# Implementa python manage.py cleanup_expired_uploads para remover documentos temporários cujo prazo expirou.
from django.core.management.base import BaseCommand

from despesas.document_extraction import TemporaryUploadStore


class Command(BaseCommand):
    help='Remove arquivos temporários de extração que expiraram.'

    def handle(self,*args,**options):
        removed=TemporaryUploadStore().cleanup()
        self.stdout.write(f'Arquivos temporários expirados removidos: {removed}')
