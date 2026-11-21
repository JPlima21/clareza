# Atende às requisições do site: filtra e resume gastos, exporta CSV, cadastra/edita/exclui despesas e entrega extração e anexos com acesso por usuário.
from decimal import Decimal
from datetime import date
import csv
from django.db.models import Sum,Count
from django.core.files import File
from django.http import FileResponse,HttpResponse,Http404,JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse_lazy
from django.views.decorators.http import require_POST
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.generic import ListView,CreateView,UpdateView,DeleteView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.decorators import login_required
from .models import Expense
from .forms import ExpenseForm,FilterForm
from .document_extraction import DocumentError,DocumentExtractor,InvalidDocumentError,TemporaryUploadStore
class OwnedMixin(LoginRequiredMixin):
    model=Expense
    def get_queryset(self): return Expense.objects.filter(owner=self.request.user)

class ExpenseFormMixin:
    def get_form_kwargs(self):
        kwargs=super().get_form_kwargs()
        kwargs['user']=self.request.user
        return kwargs
class Dashboard(OwnedMixin,ListView):
    template_name='dashboard.html'
    paginate_by=20
    def get_queryset(self):
        qs=super().get_queryset()
        self.filters=FilterForm(self.request.GET or {'start':date.today().replace(day=1).isoformat(),'end':date.today().isoformat()})
        if not self.filters.is_valid(): return qs.none()
        d=self.filters.cleaned_data
        if d.get('start'): qs=qs.filter(paid_at__gte=d['start'])
        if d.get('end'): qs=qs.filter(paid_at__lte=d['end'])
        if d.get('category'): qs=qs.filter(category=d['category'])
        if d.get('q'): qs=qs.filter(description__icontains=d['q'])
        return qs
    def get(self,request,*args,**kwargs):
        if request.GET.get('export')=='csv':
            qs=self.get_queryset()
            response=HttpResponse(content_type='text/csv; charset=utf-8-sig')
            response['Content-Disposition']='attachment; filename="despesas.csv"'
            response.write('\ufeff')
            writer=csv.writer(response,delimiter=';'); writer.writerow(['Descrição','Valor','Pagamento','Categoria'])
            for e in qs:
                description=e.description
                if description.startswith(('=','+','-','@','\t','\r','\n')): description="'"+description
                writer.writerow([description,str(e.amount),e.paid_at.isoformat(),e.category])
            return response
        return super().get(request,*args,**kwargs)
    def get_context_data(self,**kwargs):
        c=super().get_context_data(**kwargs)
        qs=self.object_list
        total=qs.aggregate(total=Sum('amount'))['total'] or Decimal('0')
        count=qs.count()
        categories=list(qs.values('category').annotate(total=Sum('amount')).order_by('-total'))
        for item in categories: item['percent']=round(item['total']/total*100) if total else 0
        query=self.request.GET.copy(); query.pop('page',None); query.pop('export',None)
        c.update(filters=self.filters,total=total,count=count,average=total/count if count else 0,categories=categories,query=query.urlencode(),documents=qs.exclude(document='').count())
        return c
class ExpenseCreate(ExpenseFormMixin,OwnedMixin,CreateView):
    form_class=ExpenseForm
    template_name='form.html'
    success_url=reverse_lazy('dashboard')
    def form_valid(self,form):
        form.instance.owner=self.request.user
        token=form.cleaned_data.get('extraction_token')
        path=TemporaryUploadStore().resolve(token,self.request.user.pk) if token else None
        if path:
            with path.open('rb') as source: form.instance.document.save(path.name,File(source),save=False)
        response=super().form_valid(form)
        if path: path.unlink(missing_ok=True)
        return response
class ExpenseUpdate(ExpenseFormMixin,OwnedMixin,UpdateView):
    form_class=ExpenseForm
    template_name='form.html'
    success_url=reverse_lazy('dashboard')
class ExpenseDelete(OwnedMixin,DeleteView):
    template_name='delete.html'
    success_url=reverse_lazy('dashboard')

@login_required
@require_POST
def extract_document(request):
    upload=request.FILES.get('document')
    store=TemporaryUploadStore()
    try:
        token=store.create(upload,request.user.pk)
        path=store.resolve(token,request.user.pk)
    except DocumentError as error:
        return JsonResponse({'error':str(error)},status=400)
    try:
        result=DocumentExtractor().extract(path)
    except InvalidDocumentError as error:
        path.unlink(missing_ok=True)
        return JsonResponse({'error':str(error)},status=400)
    except DocumentError as error:
        return JsonResponse({'error':str(error),'token':token,'attachable':True})
    return JsonResponse({
        'message':'Informações extraídas. Revise antes de salvar',
        'fields':result.fields,
        'warnings':result.warnings,
        'payment_evidence':result.payment_evidence,
        'token':token,
    })

@login_required
@xframe_options_sameorigin
def attachment(request,pk):
    e=get_object_or_404(Expense,pk=pk,owner=request.user)
    if not e.document: raise Http404
    try: return FileResponse(e.document.open('rb'),as_attachment=request.GET.get('preview')!='1',filename='comprovante'+__import__('pathlib').Path(e.document.name).suffix)
    except FileNotFoundError: raise Http404
