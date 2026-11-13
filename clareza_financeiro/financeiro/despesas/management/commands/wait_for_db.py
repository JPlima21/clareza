import time

from django.core.management.base import BaseCommand,CommandError
from django.db import OperationalError,connections


class Command(BaseCommand):
    help='Aguarda o MySQL aceitar uma consulta antes de iniciar o Django.'

    def handle(self,*args,**options):
        connection=connections['default']
        for attempt in range(30):
            try:
                connection.ensure_connection()
                with connection.cursor() as cursor: cursor.execute('SELECT 1')
                self.stdout.write(self.style.SUCCESS('Banco de dados disponível.'))
                return
            except OperationalError:
                connection.close()
                if attempt<29: time.sleep(2)
        raise CommandError('O banco de dados não ficou disponível em 60 segundos.')
