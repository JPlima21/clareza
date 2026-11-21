# Reúne testes automatizados do cadastro, validações, extração e isolamento entre usuários, usando documentos sintéticos.
from decimal import Decimal
from io import BytesIO
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from unittest import skipUnless
from unittest.mock import Mock,patch

from django.test import Client,TestCase
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image,ImageDraw,ImageFont
from pypdf import PdfWriter
from .models import Expense
from .forms import ExpenseForm
from .document_extraction import DocumentError,DocumentExtractor,ExtractionResult,TemporaryUploadStore


def synthetic_pdf(name='documento.pdf',encrypted=False):
    content=BytesIO()
    writer=PdfWriter()
    writer.add_blank_page(width=300,height=300)
    if encrypted: writer.encrypt('senha-de-teste')
    writer.write(content)
    return SimpleUploadedFile(name,content.getvalue(),content_type='application/pdf')


def synthetic_png(name='documento.png'):
    content=BytesIO()
    Image.new('RGB',(20,20),'white').save(content,'PNG')
    return SimpleUploadedFile(name,content.getvalue(),content_type='image/png')


class FinanceFlowTest(TestCase):
    def test_saved_attachment_preview_is_private_and_inline(self):
        with TemporaryDirectory() as temporary, self.settings(MEDIA_ROOT=Path(temporary)):
            owner=get_user_model().objects.create_user('preview-owner')
            other=get_user_model().objects.create_user('preview-other')
            expense=Expense.objects.create(owner=owner,description='Comprovante',amount='10',paid_at='2026-11-07',category='Outros',document=synthetic_pdf())
            url=f'/despesa/{expense.pk}/anexo/'
            self.assertEqual(self.client.get(url+'?preview=1').status_code,302)
            self.client.force_login(owner)
            self.assertContains(self.client.get(f'/despesa/{expense.pk}/'),f'data-saved-url="{url}?preview=1"')
            response=self.client.get(url+'?preview=1')
            self.assertEqual(response.status_code,200)
            self.assertEqual(response['Content-Type'],'application/pdf')
            self.assertTrue(response['Content-Disposition'].startswith('inline;'))
            self.assertEqual(response['X-Frame-Options'],'SAMEORIGIN')
            response.close()
            download=self.client.get(url)
            self.assertTrue(download['Content-Disposition'].startswith('attachment;'))
            download.close()
            self.client.force_login(other)
            self.assertEqual(self.client.get(url+'?preview=1').status_code,404)

    def test_paid_expenses_and_isolation(self):
        a=get_user_model().objects.create_user('joao',password='test-password')
        b=get_user_model().objects.create_user('outro',password='test-password')
        hidden=Expense.objects.create(owner=b,description='Privada',amount='500',paid_at='2026-11-02',category='Outros')
        self.assertEqual(self.client.get('/').status_code,302)
        self.client.force_login(a)
        response=self.client.post('/nova/',{'description':'Internet','amount':'99.90','paid_at':'2026-11-02','category':'Serviços','notes':''})
        self.assertEqual(response.status_code,302)
        e=Expense.objects.get(owner=a)
        self.assertEqual(e.amount,Decimal('99.90'))
        response=self.client.get('/?start=2026-11-01&end=2026-11-30')
        self.assertEqual(response.context['total'],Decimal('99.90'))
        self.assertEqual(response.context['count'],1)
        self.assertNotContains(response,'Privada')
        self.assertEqual(self.client.get(f'/despesa/{hidden.pk}/').status_code,404)
        self.assertEqual(self.client.get(f'/despesa/{hidden.pk}/anexo/').status_code,404)
        self.assertEqual(self.client.post(f'/despesa/{hidden.pk}/excluir/').status_code,404)
        self.assertContains(self.client.get('/?start=2026-12-01&end=2026-11-01'),'A data inicial')
        self.assertIn(b'Internet',self.client.get('/?export=csv&start=2026-11-01').content)
        self.client.post(f'/despesa/{e.pk}/excluir/')
        self.assertFalse(Expense.objects.filter(pk=e.pk).exists())
    def test_amount_and_upload_validation(self):
        data={'description':'Conta','amount':'-1','paid_at':'2026-11-01','category':'Outros'}
        self.assertFalse(ExpenseForm(data).is_valid())
        data['amount']='1.01'
        bad=SimpleUploadedFile('falso.pdf',b'executavel',content_type='application/pdf')
        self.assertFalse(ExpenseForm(data,{'document':bad}).is_valid())
        good=synthetic_pdf('conta.pdf')
        self.assertTrue(ExpenseForm(data,{'document':good}).is_valid())


class DocumentExtractionTest(TestCase):
    def test_picpay_pix_layout_and_wrapped_transaction_id(self):
        transaction_id='E'+'12345678'+'202611080029'+'Abc123def45'
        result=DocumentExtractor().parse_text(
            'Comprovante de Pix\n07/nov/2026 - 21\ue09231\ue09237\n'
            'Valor\nR$ 271,00\nPara\nANA EXEMPLO\n'
            '***.111.222\ue088**\nBANCO EXEMPLO\nDe\nPAGADOR EXEMPLO\n'
            f'ID da transação\n{transaction_id[:-2]}\n{transaction_id[-2:]}\n'
            'Dados bancários do recebedor\nAG 0001 | CC 12345678\n'
            'CNPJ nº 00.000.000/0001-00\nSAC: 0800 000 0000'
        )
        self.assertTrue(result.payment_evidence)
        self.assertEqual(result.fields,{
            'beneficiary':'ANA EXEMPLO','amount':'271.00',
            'paid_at':'2026-11-07','transaction_id':transaction_id,
        })

    def test_pix_layout_does_not_treat_boleto_as_payment(self):
        result=DocumentExtractor().parse_text('BOLETO\n07/nov/2026 - 21:31:37\nValor\nR$ 271,00\nPara\nPESSOA EXEMPLO')
        self.assertFalse(result.payment_evidence)
        self.assertNotIn('amount',result.fields)
        self.assertNotIn('paid_at',result.fields)
        self.assertNotIn('beneficiary',result.fields)

    def test_payment_timestamp_without_label(self):
        result=DocumentExtractor().parse_text(
            'COMPROVANTE DE PAGAMENTO\n10 Nov, 2026 • 19:11:55 (BRT)\nValor pago: R$ 1000,00'
        )
        self.assertEqual(result.fields.get('paid_at'),'2026-11-10')
        self.assertTrue(any('cabeçalho' in warning for warning in result.warnings))

    def test_unlabelled_timestamp_requires_payment_context_and_no_date_conflict(self):
        extractor=DocumentExtractor()
        for text in (
            'BOLETO\n10 Nov, 2026 • 19:11:55 (BRT)\nValor pago: R$ 1000,00',
            'COMPROVANTE DE PAGAMENTO\nVencimento:\n10 Nov, 2026 • 19:11:55 (BRT)',
            'COMPROVANTE DE PAGAMENTO\nEmissão:\n10 Nov, 2026 • 19:11:55 (BRT)',
        ):
            with self.subTest(text=text):
                self.assertNotIn('paid_at',extractor.parse_text(text).fields)
        result=extractor.parse_text('COMPROVANTE DE PAGAMENTO\n10 Nov, 2026 • 19:11:55 (BRT)\n11 Nov, 2026 • 12:00:00 (BRT)')
        self.assertNotIn('paid_at',result.fields)
        self.assertTrue(any('mais de uma data' in warning for warning in result.warnings))

    def test_portuguese_abbreviated_payment_dates(self):
        extractor=DocumentExtractor()
        months=['Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez']
        for month_number,month in enumerate(months,1):
            for suffix in (',','.', '.,', ''):
                with self.subTest(month=month,suffix=suffix):
                    result=extractor.parse_text(f'Data do pagamento: 10 {month}{suffix} 2026 • 19:11:55 (BRT)')
                    self.assertEqual(result.fields.get('paid_at'),f'2026-{month_number:02d}-10')
        for value in ('10 nov, 2026','10 NOV, 2026','10/11/2026','2026-11-10','10 de novembro de 2026'):
            with self.subTest(value=value):
                result=extractor.parse_text(f'Data do pagamento: {value}')
                self.assertEqual(result.fields.get('paid_at'),'2026-11-10')

    def test_abbreviated_dates_reject_invalid_due_and_ambiguous_dates(self):
        extractor=DocumentExtractor()
        for value in ('31 Fev, 2026','29 Fev, 2026','00 Nov, 2026','10 Xxx, 2026'):
            with self.subTest(value=value):
                self.assertNotIn('paid_at',extractor.parse_text(f'Data do pagamento: {value}').fields)
        self.assertEqual(extractor.parse_text('Data do pagamento: 29 Fev, 2024').fields.get('paid_at'),'2024-02-29')
        self.assertNotIn('paid_at',extractor.parse_text('Vencimento: 10 Nov, 2026').fields)
        result=extractor.parse_text('Data do pagamento: 10 Nov, 2026\nData do pagamento: 11 Nov, 2026')
        self.assertNotIn('paid_at',result.fields)
        self.assertTrue(any('mais de uma data' in warning for warning in result.warnings))

    @skipUnless(shutil.which('tesseract') and Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf').exists(),'Tesseract não instalado neste ambiente')
    def test_portuguese_ocr_with_synthetic_image(self):
        with TemporaryDirectory() as temporary:
            path=Path(temporary)/'comprovante.png'
            image=Image.new('RGB',(1500,500),'white')
            font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',44)
            ImageDraw.Draw(image).multiline_text(
                (40,40),'COMPROVANTE DE PAGAMENTO\nValor pago: R$ 42,50\nData do pagamento: 10/11/2026',
                fill='black',font=font,spacing=24,
            )
            image.save(path)
            result=DocumentExtractor().extract(path)
        self.assertEqual(result.fields['amount'],'42.50')
        self.assertEqual(result.fields['paid_at'],'2026-11-10')
        self.assertTrue(result.payment_evidence)

    def test_selectable_pdf_text_is_parsed_without_ocr(self):
        text='''
        COMPROVANTE DE PAGAMENTO
        Descrição: Internet empresarial
        Beneficiário: Provedor Exemplo LTDA
        Valor pago: R$ 1.234,56
        Data do pagamento: 10/11/2026
        ID da transação: ABC123XYZ
        '''
        page=Mock(); page.extract_text.return_value=text
        reader=Mock(is_encrypted=False,pages=[page])
        with patch('despesas.document_extraction.PdfReader',return_value=reader):
            result=DocumentExtractor().extract(Path('sintetico.pdf'))
        self.assertEqual(result.fields['description'],'Internet empresarial')
        self.assertEqual(result.fields['beneficiary'],'Provedor Exemplo LTDA')
        self.assertEqual(result.fields['amount'],'1234.56')
        self.assertEqual(result.fields['paid_at'],'2026-11-10')
        self.assertEqual(result.fields['transaction_id'],'ABC123XYZ')
        self.assertEqual(result.fields['category'],'Serviços')
        self.assertTrue(result.payment_evidence)

    def test_ambiguous_paid_values_and_dates_are_not_suggested(self):
        result=DocumentExtractor().parse_text('''
        COMPROVANTE DE PAGAMENTO
        Valor pago: R$ 80,00
        Valor pago: R$ 81,00
        Data do pagamento: 10/11/2026
        Data do pagamento: 11/11/2026
        ''')
        self.assertNotIn('amount',result.fields)
        self.assertNotIn('paid_at',result.fields)
        self.assertTrue(any('mais de um valor' in warning.lower() for warning in result.warnings))
        self.assertTrue(any('mais de uma data' in warning.lower() for warning in result.warnings))

    def test_original_amount_is_not_used_as_paid_amount(self):
        result=DocumentExtractor().parse_text('''
        COMPROVANTE DE PAGAMENTO
        Data do pagamento: 10/11/2026
        Valor original: R$ 300,00
        ''')
        self.assertNotIn('amount',result.fields)
        self.assertEqual(result.fields['paid_at'],'2026-11-10')

    def test_boleto_due_date_and_original_amount_are_not_payment_proof(self):
        result=DocumentExtractor().parse_text('''
        BOLETO BANCÁRIO
        Beneficiário: Condomínio Exemplo
        Pagador: Pessoa Exemplo
        Valor pago:
        Data do pagamento:
        Vencimento: 20/11/2026
        Valor original: R$ 300,00
        ''')
        self.assertEqual(result.fields['beneficiary'],'Condomínio Exemplo')
        self.assertNotIn('amount',result.fields)
        self.assertNotIn('paid_at',result.fields)
        self.assertFalse(result.payment_evidence)
        self.assertTrue(any('não confirma' in warning for warning in result.warnings))

    def test_extraction_stages_file_and_only_save_creates_expense(self):
        with TemporaryDirectory() as temporary, self.settings(
            TEMP_UPLOAD_ROOT=Path(temporary)/'private',MEDIA_ROOT=Path(temporary)/'media'
        ):
            user=get_user_model().objects.create_user('extrator',password='test-password')
            other=get_user_model().objects.create_user('visitante',password='test-password')
            self.client.force_login(user)
            synthetic=synthetic_png('comprovante.png')
            extracted=ExtractionResult(
                fields={'description':'Conta teste','amount':'42.50','paid_at':'2026-11-10','category':'Serviços'},
                payment_evidence=True,
            )
            with patch('despesas.views.DocumentExtractor.extract',return_value=extracted):
                response=self.client.post('/nova/extrair/',{'document':synthetic})
            self.assertEqual(response.status_code,200)
            token=response.json()['token']
            self.assertFalse(Expense.objects.exists())

            self.client.force_login(other)
            rejected=self.client.post('/nova/',{
                'description':'Tentativa','amount':'42.50','paid_at':'2026-11-10','category':'Serviços',
                'notes':'','extraction_token':token,
            })
            self.assertEqual(rejected.status_code,200)
            self.assertFalse(Expense.objects.exists())

            self.client.force_login(user)
            saved=self.client.post('/nova/',{
                'description':'Conta teste','beneficiary':'','amount':'42.50','paid_at':'2026-11-10',
                'category':'Serviços','transaction_id':'','notes':'','extraction_token':token,
            })
            self.assertEqual(saved.status_code,302)
            expense=Expense.objects.get(owner=user)
            self.assertEqual(expense.amount,Decimal('42.50'))
            self.assertTrue(expense.document.name.endswith('.png'))
            with self.assertRaises(DocumentError): TemporaryUploadStore().resolve(token,user.pk)
            original_document=expense.document.name
            edited=self.client.post(f'/despesa/{expense.pk}/',{
                'description':'Conta teste revisada','beneficiary':'','amount':'42.50','paid_at':'2026-11-10',
                'category':'Serviços','transaction_id':'','notes':'',
            })
            self.assertEqual(edited.status_code,302)
            expense.refresh_from_db()
            self.assertEqual(expense.document.name,original_document)
            self.client.force_login(other)
            self.assertEqual(self.client.get(f'/despesa/{expense.pk}/anexo/').status_code,404)

    def test_invalid_file_authentication_csrf_and_recoverable_protected_pdf(self):
        user=get_user_model().objects.create_user('seguro',password='test-password')
        invalid=SimpleUploadedFile('falso.pdf',b'not a pdf',content_type='application/pdf')
        self.assertEqual(self.client.post('/nova/extrair/',{'document':invalid}).status_code,302)
        self.client.force_login(user)
        self.assertEqual(self.client.post('/nova/extrair/',{'document':invalid}).status_code,400)

        csrf_client=Client(enforce_csrf_checks=True)
        csrf_client.force_login(user)
        valid=synthetic_pdf('protegido.pdf',encrypted=True)
        self.assertEqual(csrf_client.post('/nova/extrair/',{'document':valid}).status_code,403)

        with TemporaryDirectory() as temporary, self.settings(TEMP_UPLOAD_ROOT=Path(temporary)):
            response=self.client.post('/nova/extrair/',{'document':synthetic_pdf('protegido.pdf',encrypted=True)})
            self.assertEqual(response.status_code,200)
            self.assertTrue(response.json()['attachable'])
            self.assertTrue(response.json()['token'])
