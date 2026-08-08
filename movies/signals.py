import logging
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Booking
from .ticket_service import generate_and_save_ticket

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Booking)
def auto_generate_ticket_and_email_on_confirmation(sender, instance, created, **kwargs):
    """
    Automatically generate PDF ticket and dispatch Celery background email task
    when a Booking status is 'confirmed'.
    The HTTP response returns immediately while email delivery happens in background.
    """
    # Prevent signal recursion when updating email/ticket tracking fields in tasks
    update_fields = kwargs.get('update_fields')
    if update_fields:
        tracking_fields = {'email_status', 'email_attempts', 'email_last_error', 'email_sent_at', 'ticket'}
        if set(update_fields).intersection(tracking_fields):
            return

    if instance.status == 'confirmed':
        # Step 1: Ensure PDF ticket is generated
        if not instance.ticket:
            generate_and_save_ticket(instance)

        # Step 2: Dispatch Celery background task if email hasn't been processed yet
        if instance.email_status == 'pending' and instance.email_attempts == 0:
            try:
                from .tasks import send_ticket_email_task
                from django.conf import settings
                if getattr(settings, 'CELERY_TASK_ALWAYS_EAGER', False):
                    send_ticket_email_task(instance.id)
                else:
                    send_ticket_email_task.delay(instance.id)
                logger.info(f"Dispatched Celery email task for booking {instance.booking_reference}")
            except Exception as e:
                # Log error if Celery broker dispatch fails without interrupting HTTP response/booking
                logger.error(f"Failed to dispatch Celery email task for booking {instance.booking_reference}: {e}", exc_info=True)
