# Centraliza a configuração do Django: banco, autenticação, idioma, templates, arquivos e limites de upload.
import os
from pathlib import Path
BASE_DIR=Path(__file__).resolve().parent.parent
SECRET_KEY=os.environ['SECRET_KEY']
DEBUG=os.getenv('DEBUG','0')=='1'
ALLOWED_HOSTS=os.getenv('ALLOWED_HOSTS','localhost,127.0.0.1').split(',')
INSTALLED_APPS=['django.contrib.auth','django.contrib.contenttypes','django.contrib.sessions','django.contrib.messages','django.contrib.staticfiles','despesas']
MIDDLEWARE=['django.middleware.security.SecurityMiddleware','django.contrib.sessions.middleware.SessionMiddleware','django.middleware.common.CommonMiddleware','django.middleware.csrf.CsrfViewMiddleware','django.contrib.auth.middleware.AuthenticationMiddleware','django.contrib.messages.middleware.MessageMiddleware','django.middleware.clickjacking.XFrameOptionsMiddleware']
ROOT_URLCONF='config.urls'
TEMPLATES=[{'BACKEND':'django.template.backends.django.DjangoTemplates','DIRS':[BASE_DIR/'templates'],'APP_DIRS':True,'OPTIONS':{'context_processors':['django.template.context_processors.request','django.contrib.auth.context_processors.auth','django.contrib.messages.context_processors.messages']}}]
DATABASES={'default':{'ENGINE':'django.db.backends.mysql','NAME':os.getenv('MYSQL_DATABASE','financeiro'),'USER':os.getenv('MYSQL_USER','financeiro'),'PASSWORD':os.environ.get('MYSQL_PASSWORD',''),'HOST':os.getenv('MYSQL_HOST','db'),'PORT':'3306','OPTIONS':{'charset':'utf8mb4'}}}
if os.getenv('TEST_SQLITE')=='1': DATABASES={'default':{'ENGINE':'django.db.backends.sqlite3','NAME':BASE_DIR/'test.sqlite3'}}
AUTH_PASSWORD_VALIDATORS=[{'NAME':'django.contrib.auth.password_validation.MinimumLengthValidator'}]
LANGUAGE_CODE='pt-br'
TIME_ZONE='America/Fortaleza'
USE_TZ=True
STATIC_URL='/static/'
STATICFILES_DIRS=[BASE_DIR/'static']
MEDIA_ROOT=BASE_DIR/'media'
TEMP_UPLOAD_ROOT=BASE_DIR/'private_uploads'
LOGIN_URL='/login/'
LOGIN_REDIRECT_URL='/'
LOGOUT_REDIRECT_URL='/login/'
DEFAULT_AUTO_FIELD='django.db.models.BigAutoField'
DATA_UPLOAD_MAX_MEMORY_SIZE=12*1024*1024
FILE_UPLOAD_MAX_MEMORY_SIZE=10*1024*1024

TELEGRAM_BOT_TOKEN=os.getenv('TELEGRAM_BOT_TOKEN','')
TELEGRAM_BOT_USERNAME=os.getenv('TELEGRAM_BOT_USERNAME','').lstrip('@')
