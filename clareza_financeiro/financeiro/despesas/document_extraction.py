from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import unicodedata
import uuid
import warnings

from django.conf import settings
from django.core import signing
from PIL import Image, ImageOps
from pypdf import PdfReader
from pypdf.errors import PdfReadError


MAX_FILE_SIZE=10*1024*1024
MAX_PDF_PAGES=5
MAX_IMAGE_PIXELS=20_000_000
MAX_IMAGE_SIDE=6000
PROCESSING_TIMEOUT=30
TEMP_UPLOAD_MAX_AGE=60*60
ALLOWED_SUFFIXES={'.pdf','.png','.jpg','.jpeg'}


class DocumentError(Exception): pass
class InvalidDocumentError(DocumentError): pass
class ProtectedDocumentError(DocumentError): pass
class ProcessingLimitError(DocumentError): pass
class UnreadableDocumentError(DocumentError): pass


def validate_upload(upload):
    if not upload or not getattr(upload,'name',''):
        raise InvalidDocumentError('Selecione um arquivo PDF, JPG ou PNG.')
    if upload.size>MAX_FILE_SIZE:
        raise InvalidDocumentError('O arquivo deve ter até 10 MB.')
    suffix=Path(upload.name).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise InvalidDocumentError('Envie um arquivo PDF, PNG ou JPEG válido.')
    position=upload.tell()
    upload.seek(0)
    signature=upload.read(8)
    upload.seek(position)
    valid=(
        (suffix=='.pdf' and signature.startswith(b'%PDF-')) or
        (suffix=='.png' and signature==b'\x89PNG\r\n\x1a\n') or
        (suffix in {'.jpg','.jpeg'} and signature.startswith(b'\xff\xd8\xff'))
    )
    if not valid:
        raise InvalidDocumentError('O conteúdo não corresponde a um PDF, PNG ou JPEG válido.')
    upload.seek(0)
    try:
        if suffix=='.pdf':
            reader=PdfReader(upload,strict=False)
            if not reader.is_encrypted and not reader.pages: raise InvalidDocumentError('O PDF não contém páginas.')
        else:
            with warnings.catch_warnings():
                warnings.simplefilter('error',Image.DecompressionBombWarning)
                with Image.open(upload) as image:
                    expected={'.png':'PNG','.jpg':'JPEG','.jpeg':'JPEG'}[suffix]
                    if image.format!=expected: raise InvalidDocumentError('A imagem está inválida ou não corresponde à extensão.')
                    width,height=image.size
                    if width*height>MAX_IMAGE_PIXELS or max(width,height)>MAX_IMAGE_SIDE:
                        raise ProcessingLimitError('A imagem excede o limite de 20 megapixels ou 6000 pixels por lado.')
                    image.verify()
    except (InvalidDocumentError,ProcessingLimitError): raise
    except (PdfReadError,ValueError,OSError,SyntaxError,Image.DecompressionBombError,Image.DecompressionBombWarning):
        raise InvalidDocumentError('O arquivo está inválido ou corrompido.')
    finally:
        upload.seek(position)


@dataclass
class ExtractionResult:
    fields: dict[str,str]=field(default_factory=dict)
    warnings: list[str]=field(default_factory=list)
    payment_evidence: bool=False


class TemporaryUploadStore:
    salt='expense-document'

    @property
    def root(self): return Path(settings.TEMP_UPLOAD_ROOT)

    def create(self,upload,user_id):
        validate_upload(upload)
        self.cleanup()
        directory=self.root/str(user_id)
        directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        name=f'{uuid.uuid4().hex}{Path(upload.name).suffix.lower()}'
        path=directory/name
        upload.seek(0)
        try:
            with path.open('xb') as destination:
                for chunk in upload.chunks(): destination.write(chunk)
            try: path.chmod(0o600)
            except OSError: pass
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return signing.dumps({'user':user_id,'name':name},salt=self.salt)

    def resolve(self,token,user_id):
        try:
            data=signing.loads(token,salt=self.salt,max_age=TEMP_UPLOAD_MAX_AGE)
        except signing.SignatureExpired:
            raise DocumentError('O arquivo extraído expirou. Selecione-o novamente.')
        except signing.BadSignature:
            raise DocumentError('O arquivo extraído não pôde ser validado.')
        if data.get('user')!=user_id or not re.fullmatch(r'[0-9a-f]{32}\.(?:pdf|png|jpe?g)',data.get('name','')):
            raise DocumentError('O arquivo extraído não pertence a este usuário.')
        directory=(self.root/str(user_id)).resolve()
        path=(directory/data['name']).resolve()
        if path.parent!=directory or not path.is_file():
            raise DocumentError('O arquivo extraído não está mais disponível.')
        return path

    def delete(self,token,user_id):
        self.resolve(token,user_id).unlink(missing_ok=True)

    def cleanup(self):
        if not self.root.exists(): return 0
        cutoff=time.time()-TEMP_UPLOAD_MAX_AGE
        removed=0
        for path in self.root.glob('*/*'):
            try:
                if path.is_file() and path.stat().st_mtime<cutoff:
                    path.unlink()
                    removed+=1
            except OSError: pass
        for directory in self.root.glob('*'):
            try: directory.rmdir()
            except OSError: pass
        return removed


class DocumentExtractor:
    money_pattern=re.compile(r'(?<!\d)(?:R\$\s*)?(\d{1,3}(?:\.\d{3})*,\d{2}|\d+[,.]\d{2})(?!\d)',re.I)
    date_pattern=re.compile(
            r'(?<!\d)('
            r'\d{2}/\d{2}/\d{4}'
            r'|\d{1,2}/(?:jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)/\d{4}'
            r'|\d{4}-\d{2}-\d{2}'
            r'|\d{1,2}\s+de\s+'
            r'(?:janeiro|fevereiro|março|marco|abril|maio|junho|'
            r'julho|agosto|setembro|outubro|novembro|dezembro)'
            r'\s+de\s+\d{4}'
            r'|\d{1,2}\s+'
            r'(?:jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)'
            r'\.?,?\s+\d{4}'
            r')(?!\w)',
            re.IGNORECASE,
        )
    payment_evidence_pattern=re.compile(
        r'comprovante\s+de\s+pagamento|pagamento\s+(?:efetuado|realizado|conclu[ií]do|processado)|'
        r'transa[cç][aã]o\s+(?:aprovada|conclu[ií]da)|pix\s+(?:realizado|enviado|conclu[ií]do)|'
        r'transfer[eê]ncia\s+(?:efetuada|realizada|conclu[ií]da)',re.I)

    def extract(self,path):
        deadline=time.monotonic()+PROCESSING_TIMEOUT
        suffix=path.suffix.lower()
        if suffix=='.pdf': text=self._extract_pdf(path,deadline)
        else: text=self._ocr_image(path,deadline,check_format=True)
        text=text[:100_000].strip()
        if len(re.sub(r'\W','',text))<8:
            raise UnreadableDocumentError('Não foi possível ler o arquivo. Continue preenchendo manualmente ou selecione uma imagem mais nítida.')
        return self.parse_text(text)

    def _extract_pdf(self,path,deadline):
        try:
            reader=PdfReader(path,strict=False)
            if reader.is_encrypted:
                try: unlocked=reader.decrypt('')
                except Exception: unlocked=0
                if not unlocked: raise ProtectedDocumentError('O PDF é protegido por senha. Remova a proteção ou continue preenchendo manualmente.')
            if not reader.pages: raise InvalidDocumentError('O PDF não contém páginas.')
            if len(reader.pages)>MAX_PDF_PAGES:
                raise ProcessingLimitError(f'O PDF excede o limite de {MAX_PDF_PAGES} páginas para extração. Continue preenchendo manualmente.')
            page_texts=[]
            for page in reader.pages:
                self._remaining(deadline)
                try: page_texts.append((page.extract_text() or '').strip())
                except Exception: page_texts.append('')
                self._remaining(deadline)
        except (ProtectedDocumentError,ProcessingLimitError,InvalidDocumentError): raise
        except (PdfReadError,ValueError,OSError):
            raise InvalidDocumentError('O PDF está inválido ou corrompido.')
        if all(len(re.sub(r'\W','',text))>=20 for text in page_texts): return '\n'.join(page_texts)
        images=self._render_pdf(path,len(page_texts),deadline)
        try:
            texts=[]
            for index,image in enumerate(images):
                texts.append(page_texts[index] if len(re.sub(r'\W','',page_texts[index]))>=20 else self._ocr_image(image,deadline))
            return '\n'.join(texts)
        finally:
            for image in images: image.unlink(missing_ok=True)

    def _render_pdf(self,path,page_count,deadline):
        remaining=self._remaining(deadline)
        temporary=Path(tempfile.mkdtemp(prefix='clareza-pdf-'))
        try:
            prefix=temporary/'page'
            command=['pdftoppm','-f','1','-l',str(page_count),'-r','180','-scale-to','3000','-jpeg',str(path),str(prefix)]
            result=subprocess.run(command,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=remaining,check=False)
            images=sorted(temporary.glob('page-*.jpg'))
            if result.returncode or len(images)!=page_count:
                raise UnreadableDocumentError('Não foi possível converter as páginas escaneadas. Continue preenchendo manualmente.')
            copies=[]
            for image in images:
                handle,copy_name=tempfile.mkstemp(prefix='clareza-page-',suffix='.jpg')
                os.close(handle)
                copy=Path(copy_name)
                shutil.copyfile(image,copy)
                copies.append(copy)
            return copies
        except FileNotFoundError:
            raise UnreadableDocumentError('A ferramenta de leitura de PDF não está disponível. Continue preenchendo manualmente.')
        except subprocess.TimeoutExpired:
            raise ProcessingLimitError('A extração excedeu o limite de tempo. Continue preenchendo manualmente.')
        finally:
            shutil.rmtree(temporary,ignore_errors=True)

    def _ocr_image(self,path,deadline,check_format=False):
        prepared=None
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error',Image.DecompressionBombWarning)
                with Image.open(path) as image:
                    if check_format:
                        expected={'.png':'PNG','.jpg':'JPEG','.jpeg':'JPEG'}[path.suffix.lower()]
                        if image.format!=expected: raise InvalidDocumentError('A imagem está inválida ou não corresponde à extensão.')
                    width,height=image.size
                    if width*height>MAX_IMAGE_PIXELS or max(width,height)>MAX_IMAGE_SIDE:
                        raise ProcessingLimitError('A imagem excede o limite de 20 megapixels ou 6000 pixels por lado. Reduza a resolução ou continue manualmente.')
                    image=ImageOps.exif_transpose(image).convert('RGB')
                    image.thumbnail((3000,3000))
                    handle,prepared_name=tempfile.mkstemp(prefix='clareza-ocr-',suffix='.png')
                    os.close(handle)
                    prepared=Path(prepared_name)
                    image.save(prepared,'PNG')
        except (InvalidDocumentError,ProcessingLimitError): raise
        except (OSError,SyntaxError,Image.DecompressionBombError,Image.DecompressionBombWarning):
            raise InvalidDocumentError('A imagem está inválida ou corrompida.')
        try:
            environment=os.environ.copy()
            environment['OMP_THREAD_LIMIT']='1'
            result=subprocess.run(
                ['tesseract',str(prepared),'stdout','-l','por','--psm','6'],
                stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=self._remaining(deadline),check=False,env=environment,
            )
            if result.returncode: raise UnreadableDocumentError('Não foi possível executar o OCR. Continue preenchendo manualmente.')
            return result.stdout.decode('utf-8',errors='replace')
        except FileNotFoundError:
            raise UnreadableDocumentError('A ferramenta de OCR não está disponível. Continue preenchendo manualmente.')
        except subprocess.TimeoutExpired:
            raise ProcessingLimitError('A extração excedeu o limite de tempo. Continue preenchendo manualmente.')
        finally:
            if prepared: prepared.unlink(missing_ok=True)

    @staticmethod
    def _remaining(deadline):
        remaining=deadline-time.monotonic()
        if remaining<=0: raise ProcessingLimitError('A extração excedeu o limite de tempo. Continue preenchendo manualmente.')
        return remaining

    def parse_text(self,text):
        # O PDF do PicPay usa um glifo privado no lugar dos dois-pontos do horário.
        text=text.replace('\ue092',':')
        lines=[' '.join(line.split()) for line in text.splitlines() if line.strip()]
        fields={}
        pix_receipt=any(re.fullmatch(r'comprovante\s+de\s+pix',line,re.I) for line in lines)
        result=ExtractionResult(fields=fields,payment_evidence=pix_receipt or bool(self.payment_evidence_pattern.search(text)))

        self._single_text_field(lines,r'(?:descri[cç][aã]o(?:\s+do\s+pagamento)?|referente\s+a|finalidade)',fields,'description',160,result,'descrição')
        beneficiary_labels=r'(?:(?:nome\s+do\s+)?benefici[aá]rio|favorecido|recebedor|destinat[aá]rio|cedente|destino)'
        if pix_receipt: beneficiary_labels=r'(?:'+beneficiary_labels+r'|para\b)'
        self._single_text_field(lines,beneficiary_labels,fields,'beneficiary',160,result,'beneficiário')
        self._single_text_field(lines,r'(?:id\s+da\s+transa[cç][aã]o|identifica[cç][aã]o\s+da\s+transa[cç][aã]o|(?:e2e|end.to.end)\s*id|c[oó]digo\s+de\s+autentica[cç][aã]o|autentica[cç][aã]o\s+banc[aá]ria|protocolo|id)',fields,'transaction_id',120,result,'identificação da transação')
        if pix_receipt and fields.get('transaction_id'):
            transaction_id=fields['transaction_id']
            for index,line in enumerate(lines[:-1]):
                if line==transaction_id and re.fullmatch(r'E\d{8}\d{12}[A-Za-z0-9]{1,11}',line):
                    continuation=lines[index+1]
                    if re.fullmatch(r'[A-Za-z0-9]+',continuation) and len(line+continuation)==32:
                        fields['transaction_id']=line+continuation

        amount_values=[]
        for value in self._after_labels(lines,r'(?:valor\s+(?:efetivamente\s+)?pago|valor\s+(?:da\s+)?(?:transa[cç][aã]o|pagamento)|total\s+pago)'):
            match=self.money_pattern.search(value)
            if match:
                parsed=self._decimal(match.group(1))
                if parsed is not None: amount_values.append(parsed)
        paid_dates=[]
        for value in self._after_labels(lines,r'(?:data(?:\s+e\s+hora)?\s+(?:do\s+pagamento|da\s+transa[cç][aã]o)|pago\s+em|pagamento\s+(?:realizado|efetuado)\s+em|efetivado\s+em)'):
            match=self.date_pattern.search(value)
            if match:
                parsed=self._date(match.group(1))
                if parsed: paid_dates.append(parsed)

        if not paid_dates and result.payment_evidence:
            # Cabeçalhos de comprovantes podem trazer apenas data e hora, sem rótulo.
            document_dates=set()
            header_dates=set()
            other_date_label=re.compile(r'vencimento|emiss[aã]o|agendad[oa]|agendamento|nascimento',re.I)
            for index,line in enumerate(lines):
                for match in self.date_pattern.finditer(line):
                    parsed=self._date(match.group(1))
                    if not parsed: continue
                    document_dates.add(parsed)
                    standalone=not line[:match.start()].strip()
                    timestamp=re.fullmatch(r'\s*[•·|,\-–]?\s*\d{2}:\d{2}(?::\d{2})?(?:\s*\([A-Z]{2,5}\))?\s*',line[match.end():],re.I)
                    previous_label=index>0 and other_date_label.search(lines[index-1])
                    if standalone and timestamp and not previous_label: header_dates.add(parsed)
            if header_dates:
                if len(document_dates)==1:
                    paid_dates=list(header_dates)
                    result.warnings.append('A data foi sugerida a partir do cabeçalho do comprovante. Confirme se corresponde ao pagamento.')
                else:
                    result.warnings.append('Há mais de uma data no documento; confirme a data do pagamento manualmente.')

        if amount_values or paid_dates: result.payment_evidence=True
        if not amount_values and result.payment_evidence:
            excluded=re.compile(r'valor\s+(?:original|do\s+documento|nominal|do\s+boleto)|desconto|juros|multa',re.I)
            generic=[]
            for index,line in enumerate(lines):
                if excluded.search(line) or (index and excluded.search(lines[index-1])): continue
                generic.extend(self._decimal(match.group(1)) for match in self.money_pattern.finditer(line))
            generic=[value for value in generic if value is not None]
            if len(set(generic))==1: amount_values=generic
            elif len(set(generic))>1: result.warnings.append('Há mais de um valor no documento e nenhum está identificado claramente como valor pago.')
        unique_amounts=set(amount_values)
        if len(unique_amounts)==1: fields['amount']=format(unique_amounts.pop(),'.2f')
        elif len(unique_amounts)>1: result.warnings.append('Há mais de um valor identificado como pago; confirme o valor manualmente.')

        unique_dates=set(paid_dates)
        if len(unique_dates)==1: fields['paid_at']=unique_dates.pop()
        elif len(unique_dates)>1: result.warnings.append('Há mais de uma data identificada como pagamento; confirme a data manualmente.')

        category=self._category(text,result)
        if category: fields['category']=category
        if not result.payment_evidence:
            fields.pop('amount',None); fields.pop('paid_at',None)
            result.warnings.append('O documento não apresenta evidência clara de pagamento. Um boleto sozinho não confirma que a despesa foi paga.')
        if not fields: result.warnings.append('Não foi possível identificar informações suficientes. Preencha os campos manualmente.')
        return result

    def _single_text_field(self,lines,labels,fields,key,max_length,result,label):
        values=[]
        for value in self._after_labels(lines,labels):
            cleaned=value.strip(' :-|')[:max_length]
            if cleaned and not self.money_pattern.fullmatch(cleaned): values.append(cleaned)
        unique=list(dict.fromkeys(values))
        if len(unique)==1: fields[key]=unique[0]
        elif len(unique)>1: result.warnings.append(f'Há mais de um {label} no documento; confirme o campo manualmente.')

    @staticmethod
    def _after_labels(lines,labels):
        pattern=re.compile(rf'^\s*{labels}\s*(?:[:\-]\s*)?(.*)$',re.I)
        values=[]
        for index,line in enumerate(lines):
            match=pattern.match(line)
            if not match: continue
            value=match.group(1).strip()
            if not value and index+1<len(lines):
                candidate=lines[index+1].strip()
                if not re.match(r'^[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ /_-]{1,60}:',candidate): value=candidate
            if value: values.append(value)
        return values

    @staticmethod
    def _decimal(value):
        normalized=value.replace('.','').replace(',','.') if ',' in value else value
        try:
            amount=Decimal(normalized).quantize(Decimal('0.01'))
            return amount if Decimal('0')<amount<=Decimal('9999999999.99') else None
        except InvalidOperation: return None

    @staticmethod
    def _date(value):
        value = ' '.join(value.strip().lower().split())
        if re.fullmatch(r'\d{1,2}/[a-z]{3}/\d{4}',value): value=value.replace('/',' ')

        for fmt in ('%d/%m/%Y', '%Y-%m-%d'):
            try:
                return datetime.strptime(value, fmt).date().isoformat()
            except ValueError:
                pass

        months = {
            'janeiro': 1,
            'fevereiro': 2,
            'março': 3,
            'marco': 3,
            'abril': 4,
            'maio': 5,
            'junho': 6,
            'julho': 7,
            'agosto': 8,
            'setembro': 9,
            'outubro': 10,
            'novembro': 11,
            'dezembro': 12,
        }
        months.update({name[:3]: number for name, number in list(months.items())})

        match = re.fullmatch(
            r'(\d{1,2})(?: de)? ([a-zç]+)\.?,?(?: de)? (\d{4})',
            value,
        )
        if not match:
            return None

        day, month_name, year = match.groups()
        month = months.get(month_name)
        if month is None:
            return None

        try:
            return datetime(int(year), month, int(day)).date().isoformat()
        except ValueError:
            return None

    @staticmethod
    def _category(text,result):
        normalized=''.join(char for char in unicodedata.normalize('NFKD',text.lower()) if not unicodedata.combining(char))
        keywords={
            'Moradia':('aluguel','condominio','imobiliaria'),
            'Alimentação':('supermercado','restaurante','padaria','alimentacao'),
            'Transporte':('combustivel','posto de gasolina','uber','99app','passagem'),
            'Saúde':('farmacia','hospital','clinica medica','plano de saude'),
            'Educação':('mensalidade escolar','faculdade','curso','livraria'),
            'Lazer':('cinema','teatro','streaming','ingresso'),
            'Serviços':('internet','telefonia','contabilidade','software'),
        }
        matches=[category for category,terms in keywords.items() if any(term in normalized for term in terms)]
        if len(matches)==1: return matches[0]
        if len(matches)>1: result.warnings.append('O documento sugere mais de uma categoria; selecione a categoria manualmente.')
        return None
