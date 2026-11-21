# Acrescenta beneficiário e identificação da transação e atualiza o rótulo do valor, preservando os registros existentes.
from decimal import Decimal

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('despesas', '0001_initial')]

    operations = [
        migrations.AddField(
            model_name='expense',
            name='beneficiary',
            field=models.CharField(blank=True, max_length=160, verbose_name='Beneficiário'),
        ),
        migrations.AddField(
            model_name='expense',
            name='transaction_id',
            field=models.CharField(blank=True, max_length=120, verbose_name='Identificação da transação'),
        ),
        migrations.AlterField(
            model_name='expense',
            name='amount',
            field=models.DecimalField(decimal_places=2, max_digits=12, validators=[django.core.validators.MinValueValidator(Decimal('0.01'))], verbose_name='Valor pago (R$)'),
        ),
    ]
