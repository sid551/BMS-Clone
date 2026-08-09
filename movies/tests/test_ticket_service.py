from unittest.mock import patch
from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.utils import timezone
from movies.models import Movie, Theater, Screen, ShowSchedule, Seat, Booking, BookingSeat, Payment
from movies.ticket_service import generate_ticket_pdf, generate_and_save_ticket


class TicketGenerationTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser',
            password='password123',
            email='testuser@example.com'
        )
        self.movie = Movie.objects.create(
            title='Inception',
            duration_minutes=148,
            age_certification='U/A'
        )
        self.theater = Theater.objects.create(
            name='PVR Cinemas',
            location='Downtown Mall'
        )
        self.screen = Screen.objects.create(
            theater=self.theater,
            name='Screen 1 - IMAX',
            total_rows=5,
            seats_per_row=5
        )
        self.schedule = ShowSchedule.objects.create(
            movie=self.movie,
            theater=self.theater,
            screen=self.screen,
            show_time=timezone.now() + timezone.timedelta(days=1),
            price=250.00
        )
        self.seat = Seat.objects.create(
            theater=self.theater,
            screen=self.screen,
            row='A',
            number=1,
            seat_number='A1'
        )
        self.client = Client()
        self.client.login(username='testuser', password='password123')

    def test_pdf_ticket_content_generation(self):
        """Test direct generation of PDF ticket buffer."""
        booking = Booking.objects.create(
            user=self.user,
            show_schedule=self.schedule,
            movie=self.movie,
            theater=self.theater,
            number_of_seats=1,
            total_price=250.00,
            status='confirmed'
        )
        BookingSeat.objects.create(
            booking=booking,
            show_schedule=self.schedule,
            seat=self.seat,
            price=250.00
        )

        pdf_content = generate_ticket_pdf(booking)
        self.assertIsNotNone(pdf_content)
        pdf_bytes = pdf_content.read()
        self.assertTrue(pdf_bytes.startswith(b'%PDF-'))
        self.assertGreater(len(pdf_bytes), 500)

    def test_auto_generate_ticket_on_confirmation(self):
        """Test signal triggers automated ticket PDF save on confirmed status."""
        booking = Booking.objects.create(
            user=self.user,
            show_schedule=self.schedule,
            movie=self.movie,
            theater=self.theater,
            number_of_seats=1,
            total_price=250.00,
            status='confirmed'
        )

        booking.refresh_from_db()
        self.assertTrue(bool(booking.ticket))
        self.assertTrue(booking.ticket.name.endswith('.pdf'))
        self.assertTrue(booking.ticket.storage.exists(booking.ticket.name))

    def test_pending_booking_does_not_generate_ticket(self):
        """Pending bookings should not generate a ticket PDF."""
        booking = Booking.objects.create(
            user=self.user,
            show_schedule=self.schedule,
            movie=self.movie,
            theater=self.theater,
            number_of_seats=1,
            total_price=250.00,
            status='pending'
        )

        booking.refresh_from_db()
        self.assertFalse(bool(booking.ticket))

    def test_error_isolation_on_ticket_generation_failure(self):
        """If PDF generation fails, error is logged and booking remains confirmed."""
        booking = Booking.objects.create(
            user=self.user,
            show_schedule=self.schedule,
            movie=self.movie,
            theater=self.theater,
            number_of_seats=1,
            total_price=250.00,
            status='pending'
        )

        with patch('movies.ticket_service.generate_ticket_pdf', side_effect=Exception('PDF Render Engine Crash')):
            booking.status = 'confirmed'
            booking.save()

            # Confirm status was not changed despite PDF failure
            booking.refresh_from_db()
            self.assertEqual(booking.status, 'confirmed')

    def test_download_ticket_pdf_view(self):
        """Test HTTP GET endpoint serving generated ticket PDF."""
        booking = Booking.objects.create(
            user=self.user,
            show_schedule=self.schedule,
            movie=self.movie,
            theater=self.theater,
            number_of_seats=1,
            total_price=250.00,
            status='confirmed'
        )

        url = f'/movies/booking/{booking.booking_reference}/ticket/'
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(response.content.startswith(b'%PDF-'))

    def test_pdf_ticket_generation_with_cloudinary_storage_path_error(self):
        """Test PDF ticket generation when image field raises NotImplementedError on .path (e.g. Cloudinary)."""
        booking = Booking.objects.create(
            user=self.user,
            show_schedule=self.schedule,
            movie=self.movie,
            theater=self.theater,
            number_of_seats=1,
            total_price=250.00,
            status='confirmed'
        )

        class DummyCloudinaryPoster:
            name = 'posters/test.jpg'
            url = 'https://res.cloudinary.com/demo/image/upload/sample.jpg'
            @property
            def path(self):
                raise NotImplementedError("This backend doesn't support absolute paths.")

        booking.movie.poster = DummyCloudinaryPoster()

        # Should generate PDF smoothly using fallback without crashing
        pdf_content = generate_ticket_pdf(booking)
        self.assertIsNotNone(pdf_content)
        pdf_bytes = pdf_content.read()
        self.assertTrue(pdf_bytes.startswith(b'%PDF-'))

    def test_verify_ticket_view(self):
        """Test server-side ticket verification endpoint scanned via QR Code."""
        booking = Booking.objects.create(
            user=self.user,
            show_schedule=self.schedule,
            movie=self.movie,
            theater=self.theater,
            number_of_seats=1,
            total_price=250.00,
            status='confirmed'
        )

        url = f'/movies/booking/{booking.booking_reference}/verify/'
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'VALID TICKET')
        self.assertContains(response, booking.booking_reference)
        self.assertContains(response, 'Inception')

    def test_verify_ticket_json_api_valid(self):
        """Test server-side JSON verification endpoint for valid confirmed booking."""
        booking = Booking.objects.create(
            user=self.user,
            show_schedule=self.schedule,
            movie=self.movie,
            theater=self.theater,
            number_of_seats=1,
            total_price=250.00,
            status='confirmed'
        )

        url = f'/movies/booking/{booking.booking_reference}/verify/?format=json'
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['valid'])
        self.assertEqual(data['booking_id'], booking.booking_reference)
        self.assertEqual(data['movie'], 'Inception')
        self.assertEqual(data['theater'], 'PVR Cinemas')
        self.assertEqual(data['status'], 'Confirmed')

    def test_verify_ticket_json_api_invalid_cancelled(self):
        """Test server-side verification returns invalid status for cancelled or non-existent ticket."""
        booking = Booking.objects.create(
            user=self.user,
            show_schedule=self.schedule,
            movie=self.movie,
            theater=self.theater,
            number_of_seats=1,
            total_price=250.00,
            status='cancelled'
        )

        url = f'/movies/booking/{booking.booking_reference}/verify/?format=json'
        response = self.client.get(url)

        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertFalse(data['valid'])
        self.assertEqual(data['booking_id'], booking.booking_reference)
        self.assertEqual(data['status'], 'Cancelled')



