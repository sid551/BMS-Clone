import logging
from celery import shared_task
from django.core.mail import EmailMultiAlternatives
from django.utils import timezone
from django.conf import settings
from .models import Booking
from .ticket_service import generate_and_save_ticket

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=5)
def send_ticket_email_task(self, booking_id):
    """
    Celery background task to send PDF ticket email asynchronously to the user.

    Flow:
      1. Fetch booking.
      2. Check idempotency: if email_status == 'sent', skip resending.
      3. Ensure ticket PDF exists (generate if missing).
      4. Build and dispatch EmailMessage with PDF attachment.
      5. Update email delivery tracking status (Sent/Failed/Pending, attempt counter, last error).
      6. Automatic retry on temporary failure (up to 3 retries).
    """
    try:
        booking = Booking.objects.select_related(
            'user', 'movie', 'theater',
            'show_schedule__movie', 'show_schedule__theater', 'show_schedule__screen'
        ).get(pk=booking_id)
    except Booking.DoesNotExist:
        logger.error(f"Celery email task failed: Booking with ID {booking_id} does not exist.")
        return False

    # 1. Idempotency Check — prevent duplicate emails
    if booking.email_status == 'sent':
        logger.info(f"Ticket email already sent for booking {booking.booking_reference}. Skipping duplicate task.")
        return True

    # Check recipient email
    recipient_email = booking.user.email if booking.user else ""
    if not recipient_email:
        err_msg = f"User '{booking.user.username}' has no email address configured."
        logger.warning(f"Booking {booking.booking_reference}: {err_msg}")
        booking.email_attempts += 1
        booking.email_last_error = err_msg
        booking.email_status = 'failed'
        booking.save(update_fields=['email_attempts', 'email_last_error', 'email_status', 'updated_at'])
        return False

    # 2. Ensure Ticket PDF is available (checks DB binary field ticket_pdf_data first)
    from .ticket_service import get_booking_ticket_bytes
    pdf_bytes = get_booking_ticket_bytes(booking)
    if not pdf_bytes or not pdf_bytes.startswith(b'%PDF-'):
        logger.info(f"Ticket PDF missing for booking {booking.booking_reference}. Generating on-the-fly...")
        success = generate_and_save_ticket(booking)
        if not success:
            err_msg = "PDF ticket generation failed prior to email dispatch."
            logger.error(f"Booking {booking.booking_reference}: {err_msg}")
            booking.email_attempts += 1
            booking.email_last_error = err_msg
            booking.email_status = 'failed'
            booking.save(update_fields=['email_attempts', 'email_last_error', 'email_status', 'updated_at'])
            return False
        booking.refresh_from_db()


    # Prepare metadata for email
    movie = booking.movie or (booking.show_schedule.movie if booking.show_schedule else None)
    theater = booking.theater or (booking.show_schedule.theater if booking.show_schedule else None)
    screen = booking.show_schedule.screen if (booking.show_schedule and booking.show_schedule.screen) else None

    movie_title = movie.title if movie else "Movie Ticket"
    theater_name = theater.name if theater else "Theater"
    screen_name = screen.name if screen else "Screen 1"

    if booking.show_schedule and booking.show_schedule.show_time:
        show_time_str = booking.show_schedule.show_time.strftime("%A, %d %b %Y at %I:%M %p")
    else:
        show_time_str = booking.booked_at.strftime("%A, %d %b %Y at %I:%M %p")

    booked_seats_qs = booking.booked_seats.select_related('seat').all()
    if booked_seats_qs.exists():
        seat_numbers = ", ".join([bs.seat.seat_number for bs in booked_seats_qs])
    elif booking.seat:
        seat_numbers = booking.seat.seat_number
    else:
        seat_numbers = f"{booking.number_of_seats} Seat(s)"

    # 3. Construct Email
    subject = f"Your Movie Ticket - {movie_title} ({booking.booking_reference})"
    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'tickets@bookmyshow.com')

    text_body = (
        f"Hello {booking.user.get_full_name() or booking.user.username},\n\n"
        f"Your booking for '{movie_title}' is confirmed!\n\n"
        f"Booking Reference: {booking.booking_reference}\n"
        f"Theater: {theater_name}\n"
        f"Screen: {screen_name}\n"
        f"Showtime: {show_time_str}\n"
        f"Seats: {seat_numbers}\n"
        f"Total Amount: INR {booking.total_price:,.2f}\n\n"
        f"Your official PDF ticket with entry QR code is attached to this email.\n"
        f"Thank you for booking with BookMyShow!"
    )

    html_body = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; border: 1px solid #e5e7eb; border-radius: 8px; overflow: hidden;">
      <div style="background-color: #e11d48; color: #ffffff; padding: 20px; text-align: center;">
        <h2 style="margin: 0;">BookMyShow Pass Confirmed</h2>
        <p style="margin: 5px 0 0 0;">Ref: <strong>{booking.booking_reference}</strong></p>
      </div>
      <div style="padding: 24px; color: #1f2937;">
        <p>Dear <strong>{booking.user.get_full_name() or booking.user.username}</strong>,</p>
        <p>Your movie ticket booking has been successfully confirmed. Below are your show details:</p>
        
        <table style="width: 100%; border-collapse: collapse; margin: 20px 0;">
          <tr style="border-bottom: 1px solid #f3f4f6;"><td style="padding: 8px 0; color: #4b5563; font-weight: bold;">Movie:</td><td style="padding: 8px 0; font-weight: bold; color: #111827;">{movie_title}</td></tr>
          <tr style="border-bottom: 1px solid #f3f4f6;"><td style="padding: 8px 0; color: #4b5563; font-weight: bold;">Theater:</td><td style="padding: 8px 0;">{theater_name}</td></tr>
          <tr style="border-bottom: 1px solid #f3f4f6;"><td style="padding: 8px 0; color: #4b5563; font-weight: bold;">Screen:</td><td style="padding: 8px 0;">{screen_name}</td></tr>
          <tr style="border-bottom: 1px solid #f3f4f6;"><td style="padding: 8px 0; color: #4b5563; font-weight: bold;">Showtime:</td><td style="padding: 8px 0;">{show_time_str}</td></tr>
          <tr style="border-bottom: 1px solid #f3f4f6;"><td style="padding: 8px 0; color: #4b5563; font-weight: bold;">Seats:</td><td style="padding: 8px 0; color: #e11d48; font-weight: bold;">{seat_numbers}</td></tr>
          <tr><td style="padding: 8px 0; color: #4b5563; font-weight: bold;">Total Amount:</td><td style="padding: 8px 0; font-weight: bold; color: #059669;">INR {booking.total_price:,.2f}</td></tr>
        </table>


        <div style="background-color: #f9fafb; padding: 15px; border-radius: 6px; border: 1px dashed #d1d5db; text-align: center; margin-top: 20px;">
          <p style="margin: 0; font-size: 14px; color: #374151;">
            <strong>Attached PDF Ticket:</strong> Please find your official PDF ticket attached to this email. You can present the embedded QR code at the cinema entry gate.
          </p>
        </div>
      </div>
      <div style="background-color: #f3f4f6; color: #6b7280; padding: 12px; text-align: center; font-size: 12px;">
        BookMyShow &bull; Automated Ticket Delivery System
      </div>
    </div>
    """

    # 4. Build PDF attachment
    pdf_attachment = None
    try:
        from .ticket_service import get_booking_ticket_bytes
        pdf_bytes = get_booking_ticket_bytes(booking)
        if pdf_bytes and pdf_bytes.startswith(b'%PDF-'):
            pdf_attachment = {
                'name': f'ticket_{booking.booking_reference}.pdf',
                'content': pdf_bytes,
                'type': 'application/pdf',
            }
        else:
            logger.error(f'Invalid PDF bytes generated/retrieved for booking {booking.booking_reference}')
    except Exception as e:
        logger.error(f'Failed to get ticket PDF for {booking.booking_reference}: {e}')

    # 5. Dispatch via Brevo HTTP API if key is present, else standard Django EmailMultiAlternatives
    from .brevo_service import send_email as brevo_send

    booking.email_attempts += 1
    api_key = getattr(settings, 'BREVO_API_KEY', '')

    success = False
    mail_err_msg = None
    if api_key:
        attachments = [pdf_attachment] if pdf_attachment else []
        success, mail_err_msg = brevo_send(
            to_email=recipient_email,
            to_name=booking.user.get_full_name() or booking.user.username,
            subject=subject,
            html_body=html_body,
            text_body=text_body,
            attachments=attachments,
        )
    else:
        # Check standard Django email backend if Brevo API key is not configured
        email_backend = getattr(settings, 'EMAIL_BACKEND', '')
        if 'locmem' in email_backend:
            # Unit testing backend
            try:
                email_msg = EmailMultiAlternatives(
                    subject=subject,
                    body=text_body,
                    from_email=from_email,
                    to=[recipient_email],
                )
                email_msg.attach_alternative(html_body, "text/html")
                if pdf_attachment:
                    email_msg.attach(
                        pdf_attachment['name'],
                        pdf_attachment['content'],
                        pdf_attachment['type']
                    )
                email_msg.send(fail_silently=False)
                success = True
                mail_err_msg = None
            except Exception as mail_err:
                mail_err_msg = str(mail_err)
                success = False
        elif 'console' in email_backend:
            # Serverless/console backend without Brevo key is not actual inbox delivery
            success = False
            mail_err_msg = "BREVO_API_KEY environment variable is not configured in Vercel settings. Configure BREVO_API_KEY to send emails via HTTP API."
        else:
            # SMTP backend
            try:
                email_msg = EmailMultiAlternatives(
                    subject=subject,
                    body=text_body,
                    from_email=from_email,
                    to=[recipient_email],
                )
                email_msg.attach_alternative(html_body, "text/html")
                if pdf_attachment:
                    email_msg.attach(
                        pdf_attachment['name'],
                        pdf_attachment['content'],
                        pdf_attachment['type']
                    )
                email_msg.send(fail_silently=False)
                success = True
                mail_err_msg = None
            except Exception as mail_err:
                logger.error(f"Django email dispatch failed for booking {booking.booking_reference}: {mail_err}")
                mail_err_msg = str(mail_err)
                success = False

    if success:
        booking.email_status = 'sent'
        booking.email_sent_at = timezone.now()
        booking.email_last_error = None
        booking.save(update_fields=['email_status', 'email_sent_at', 'email_attempts', 'email_last_error', 'updated_at'])
        logger.info(f'Ticket email sent to {recipient_email} for booking {booking.booking_reference}')
        return True
    else:
        err_msg = mail_err_msg or 'Email dispatch failed — see logs for details.'
        booking.email_last_error = err_msg

        if self.request.retries >= self.max_retries:
            booking.email_status = 'failed'
            booking.save(update_fields=['email_status', 'email_attempts', 'email_last_error', 'updated_at'])
            logger.warning(f'Max retries reached for booking {booking.booking_reference}. Email marked failed.')
            return False

        booking.email_status = 'pending'
        booking.save(update_fields=['email_status', 'email_attempts', 'email_last_error', 'updated_at'])
        raise self.retry(exc=Exception(err_msg), countdown=30)

