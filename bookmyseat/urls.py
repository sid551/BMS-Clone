from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.views.decorators.csrf import csrf_exempt
from movies import views as movie_views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('users/', include('users.urls')),
    path('', include('users.urls')),
    path('movies/', include('movies.urls')),
    # Webhook registered at root level with CSRF exempt — Razorpay cannot send CSRF tokens
    path('webhooks/razorpay/', csrf_exempt(movie_views.razorpay_webhook), name='razorpay_webhook_root'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
