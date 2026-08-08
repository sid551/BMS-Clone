import os
from celery import Celery
from django.conf import settings

# Set default Django settings module for 'celery' program.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bookmyseat.settings')

app = Celery('bookmyseat')

# Load Celery settings starting with 'CELERY_' prefix
app.config_from_object('django.conf:settings', namespace='CELERY')

# Configure eager execution when specified
if getattr(settings, 'CELERY_TASK_ALWAYS_EAGER', False):
    app.conf.task_always_eager = True
    app.conf.task_eager_propagates = True

# Load task modules from all registered Django apps.
app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f'Request: {self.request!r}')
