#!/bin/sh
set -e

python manage.py wait_for_db
python manage.py migrate
python manage.py cleanup_expired_uploads

(while sleep 3600; do python manage.py cleanup_expired_uploads; done) &

exec python manage.py runserver 0.0.0.0:8000
