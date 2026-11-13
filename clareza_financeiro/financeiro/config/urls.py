from django.urls import path
from django.contrib.auth.views import LoginView, LogoutView
from despesas.views import Dashboard, ExpenseCreate, ExpenseUpdate, ExpenseDelete, attachment, extract_document
urlpatterns=[path('',Dashboard.as_view(),name='dashboard'),path('nova/',ExpenseCreate.as_view(),name='create'),path('nova/extrair/',extract_document,name='extract_document'),path('despesa/<int:pk>/',ExpenseUpdate.as_view(),name='edit'),path('despesa/<int:pk>/excluir/',ExpenseDelete.as_view(),name='delete'),path('despesa/<int:pk>/anexo/',attachment,name='attachment'),path('login/',LoginView.as_view(template_name='login.html'),name='login'),path('logout/',LogoutView.as_view(),name='logout')]
