from django import forms

from .document_extraction import DocumentError, TemporaryUploadStore, validate_upload
from .models import Expense


class ExpenseForm(forms.ModelForm):
    category=forms.ChoiceField(choices=[('','Selecione uma categoria')]+Expense.CATEGORIES,label='Categoria')
    extraction_token=forms.CharField(required=False,widget=forms.HiddenInput)

    def __init__(self,*args,user=None,**kwargs):
        super().__init__(*args,**kwargs)
        self.user=user

    class Meta:
        model=Expense
        fields=['description','beneficiary','amount','paid_at','category','transaction_id','document','notes']
        widgets={'paid_at':forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}),'notes':forms.Textarea(attrs={'rows':3}),'document':forms.FileInput(attrs={'accept':'.pdf,.png,.jpg,.jpeg'})}

    def clean_document(self):
        f=self.cleaned_data.get('document')
        if f and hasattr(f,'content_type'):
            try: validate_upload(f)
            except DocumentError as error: raise forms.ValidationError(str(error))
        return f

    def clean_extraction_token(self):
        token=self.cleaned_data.get('extraction_token')
        if token:
            if not self.user: raise forms.ValidationError('Não foi possível validar o arquivo extraído.')
            try: TemporaryUploadStore().resolve(token,self.user.pk)
            except DocumentError as error: raise forms.ValidationError(str(error))
        return token


class FilterForm(forms.Form):
    start=forms.DateField(required=False,widget=forms.DateInput(attrs={'type':'date'}),label='De')
    end=forms.DateField(required=False,widget=forms.DateInput(attrs={'type':'date'}),label='Até')
    category=forms.ChoiceField(required=False,choices=[('','Todas as categorias')]+Expense.CATEGORIES,label='Categoria')
    q=forms.CharField(required=False,max_length=160,label='Buscar despesa')
    def clean(self):
        data=super().clean()
        if data.get('start') and data.get('end') and data['start']>data['end']: raise forms.ValidationError('A data inicial deve ser anterior à final.')
        return data
