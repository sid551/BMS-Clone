from unittest.mock import patch
from django.test import TestCase, override_settings
from django.contrib.auth.models import User
from django.utils import timezone
from django.core import mail
from movies.models import Movie, Theater, Screen, ShowSchedule, Seat, Booking, BookingSeat
from movies.tasks import send_ticket_email_task


@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_EAGER_PROPAGATES=True,
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend'
)
class EmailDeliveryTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='emailuser',
            password='password123',
            email='emailuser@example.com',
            first_name='John',
            last_name='Doe'
        )
        self.movie = Movie.objects.create(
            title='Interstellar',
            duration_minutes=169,
            age_certification='U/A'
        )
        self.theater = Theater.objects.create(
            name='INOX Cinema',
            location='City Center'
        )
        self.screen = Screen.objects.create(
            theater=self.theater,
            name='Screen 2',
            total_rows=5,
            seats_per_row=5
        )
        self.schedule = ShowSchedule.objects.create(
            movie=self.movie,
            theater=self.theater,
            screen=self.screen,
            show_time=timezone.now() + timezone.timedelta(days=1),
            price=300.00
        )
        self.seat = Seat.objects.create(
            theater=self.theater,
            screen=self.screen,
            row='B',
            number=5,
            seat_number='B5'
        )
        mail.outbox = []

    def test_successful_email_delivery_task(self):
        """Test asynchronous Celery email task sends ticket PDF email to user and updates status."""
        booking = Booking.objects.create(
            user=self.user,
            show_schedule=self.schedule,
            movie=self.movie,
            theater=self.theater,
            number_of_seats=1,
            total_price=300.00,
            status='confirmed'
        )
        BookingSeat.objects.create(
            booking=booking,
            show_schedule=self.schedule,
            seat=self.seat,
            price=300.00
        )

        result = send_ticket_email_task(booking.id)
        self.assertTrue(result)

        # Check outbox
        self.assertEqual(len(mail.outbox), 1)
        sent_mail = mail.outbox[0]
        self.assertEqual(sent_mail.to, ['emailuser@example.com'])
        self.assertIn('Interstellar', sent_mail.subject)
        self.assertIn(booking.booking_reference, sent_mail.subject)

        # Check attachment
        self.assertEqual(len(sent_mail.attachments), 1)
        attachment_filename, attachment_content, mime_type = sent_mail.attachments[0]
        self.assertTrue(attachment_filename.endswith('.pdf'))
        self.assertEqual(mime_type, 'application/pdf')
        self.assertTrue(attachment_content.startswith(b'%PDF-'))

        # Check Booking DB record status
        booking.refresh_from_db()
        self.assertEqual(booking.email_status, 'sent')
        self.assertEqual(booking.email_attempts, 1)
        self.assertIsNotNone(booking.email_sent_at)
        self.assertIsNone(booking.email_last_error)

    def test_idempotency_prevents_duplicate_emails(self):
        """Task should skip execution if email_status is already 'sent'."""
        booking = Booking.objects.create(
            user=self.user,
            show_schedule=self.schedule,
            movie=self.movie,
            theater=self.theater,
            number_of_seats=1,
            total_price=300.00,
            status='confirmed',
            email_status='sent'
        )

        result = send_ticket_email_task(booking.id)
        self.assertTrue(result)
        self.assertEqual(len(mail.outbox), 0)

    def test_email_retry_and_failure_handling(self):
        """Email delivery failures log errors, track attempts, set status to failed on max retries, without altering booking status."""
        booking = Booking.objects.create(
            user=self.user,
            show_schedule=self.schedule,
            movie=self.movie,
            theater=self.theater,
            number_of_seats=1,
            total_price=300.00,
            status='confirmed'
        )
        # Ensure ticket PDF exists and reset email tracking for explicit task execution test
        from movies.ticket_service import generate_and_save_ticket
        generate_and_save_ticket(booking)
        booking.email_status = 'pending'
        booking.email_attempts = 0
        booking.email_last_error = None
        booking.save(update_fields=['email_status', 'email_attempts', 'email_last_error'])

        with patch('django.core.mail.EmailMultiAlternatives.send', side_effect=Exception('SMTP Connection Timeout')):
            # Execute task with retries disabled for direct failure test
            with patch.object(send_ticket_email_task, 'max_retries', 0):
                send_ticket_email_task(booking.id)

        booking.refresh_from_db()
        self.assertEqual(booking.email_status, 'failed')
        self.assertEqual(booking.email_attempts, 1)
        self.assertIn('SMTP Connection Timeout', booking.email_last_error)

        # Confirm booking status remains confirmed
        self.assertEqual(booking.status, 'confirmed')

    def test_no_email_configured_for_user(self):
        """If user has no email address, task sets status to failed gracefully."""
        no_email_user = User.objects.create_user(
            username='noemailuser',
            password='password123',
            email=''
        )
        booking = Booking.objects.create(
            user=no_email_user,
            show_schedule=self.schedule,
            movie=self.movie,
            theater=self.theater,
            number_of_seats=1,
            total_price=300.00,
            status='confirmed'
        )

        result = send_ticket_email_task(booking.id)
        self.assertFalse(result)

        booking.refresh_from_db()
        self.assertEqual(booking.email_status, 'failed')
        self.assertIn("has no email address configured", booking.email_last_error)
        self.assertEqual(booking.status, 'confirmed')
