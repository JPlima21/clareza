# Define a despesa armazenada no banco: dono, descrição, valor, data, categoria e anexo com nome aleatório.
from django.db import models
from django.conf import settings
from django.core.validators import MinValueValidator
from decimal import Decimal
import uuid
from pathlib import Path

def attachment_path(instance, filename):
    return f'comprovantes/{instance.owner_id}/{uuid.uuid4().hex}{Path(filename).suffix.lower()}'

class Expense(models.Model):
    CATEGORIES=[(x,x) for x in ['Moradia','Alimentação','Transporte','Saúde','Educação','Lazer','Serviços','Compras','Outros']]
    owner=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE)
    description=models.CharField('Descrição',max_length=160)
    beneficiary=models.CharField('Beneficiário',max_length=160,blank=True)
    amount=models.DecimalField('Valor pago (R$)',max_digits=12,decimal_places=2,validators=[MinValueValidator(Decimal('0.01'))])
    paid_at=models.DateField('Data do pagamento')
    category=models.CharField('Categoria',max_length=30,choices=CATEGORIES)
    transaction_id=models.CharField('Identificação da transação',max_length=120,blank=True)
    document=models.FileField('Boleto ou comprovante',upload_to=attachment_path,blank=True)
    notes=models.TextField('Observações',blank=True,max_length=2000)
    class Meta:
        ordering=['-paid_at','-id']
        constraints=[models.CheckConstraint(condition=models.Q(amount__gt=0),name='positive_amount')]
    def __str__(self): return self.description


class TelegramAccount(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    telegram_id = models.BigIntegerField(unique=True, null=True, blank=True)
    code_hash = models.CharField(max_length=64, blank=True)
    code_expires_at = models.DateTimeField(null=True, blank=True)


class TelegramDraft(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    token = models.TextField()
    fields = models.JSONField(default=dict)
    warnings = models.JSONField(default=list)
    editing = models.CharField(max_length=30, blank=True)
    expires_at = models.DateTimeField()


class TelegramCursor(models.Model):
    # One row for the single polling process; never run two workers for one bot.
    offset = models.BigIntegerField(default=0)
