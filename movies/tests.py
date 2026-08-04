import json
import hmac
import hashlib
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch, MagicMock

from django.test import TestCase, Client, override_settings
from django.conf import settings
from django.contrib.auth.models import User
from django.utils import timezone
from django.urls import reverse

from movies.models import (
    Movie, Theater, Screen, Seat, ShowSchedule, ShowSeat, Booking, BookingSeat, Payment
)
from movies.reservation_service import reserve_seats


@override_settings(
    RAZORPAY_KEY_ID='rzp_test_TGC8nqUhxruhge',
    RAZORPAY_KEY_SECRET='6bVYVwV5jpiMa0FRH7xT0QPV',
    RAZORPAY_WEBHOOK_SECRET='BmsWebHook'
)
class MoviesWorkflowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='password123')
        self.user.backend = 'django.contrib.auth.backends.ModelBackend'
        self.client = Client()
        self.client.force_login(self.user)

        self.movie = Movie.objects.create(
            title='Inception',
            duration_minutes=148,
            release_date=timezone.now().date(),
            status='now_showing'
        )

        self.theater = Theater.objects.create(name='PVR Cinema', location='Downtown')
        self.screen = Screen.objects.create(
            theater=self.theater,
            name='AUDI 1',
            screen_type='IMAX_3D',
            total_rows=2,
            seats_per_row=2
        )
        self.screen.generate_seats()

        now = timezone.now()
        self.schedule = ShowSchedule.objects.create(
            movie=self.movie,
            theater=self.theater,
            screen=self.screen,
            show_time=now + timedelta(days=1),
            price=Decimal('300.00')
        )

    @patch('movies.payment_service._get_client')
    def test_book_seats_view_launches_checkout(self, mock_get_client):
        """Test POST /movies/theater/<id>/seats/book/ reserves seats and renders checkout page with Razorpay order."""
        seats = Seat.objects.filter(screen=self.screen)[:2]
        reserve_seats(self.user, self.schedule.id, [s.id for s in seats])

        mock_razorpay_client = MagicMock()
        mock_razorpay_client.order.create.return_value = {
            'id': 'order_ui_test_123',
            'amount': 60000,
            'currency': 'INR',
            'receipt': 'bms_receipt_1',
            'status': 'created',
        }
        mock_get_client.return_value = mock_razorpay_client

        url = reverse('book_seats', args=[self.theater.id])
        response = self.client.post(url, {'schedule_id': self.schedule.id, 'seats': [s.id for s in seats]})

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'movies/checkout.html')
        self.assertContains(response, 'Inception')
        self.assertContains(response, 'order_ui_test_123')

    def test_past_schedule_link_auto_falls_back_to_upcoming_schedule(self):
        """Test accessing a past/expired schedule ID automatically falls back to the next active upcoming schedule."""
        past_schedule = ShowSchedule.objects.create(
            movie=self.movie,
            theater=self.theater,
            screen=self.screen,
            show_time=timezone.now() - timedelta(days=1),
            price=Decimal('250.00')
        )

        url = f"{reverse('book_seats', args=[self.theater.id])}?schedule_id={past_schedule.id}"
        response = self.client.get(url, follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'movies/seat_selection.html')
        self.assertEqual(response.context['schedule'].id, self.schedule.id)

    def test_theater_list_filters_out_past_schedules(self):
        """Test theater list page only shows upcoming showtimes and excludes past schedules."""
        past_schedule = ShowSchedule.objects.create(
            movie=self.movie,
            theater=self.theater,
            screen=self.screen,
            show_time=timezone.now() - timedelta(days=1),
            price=Decimal('250.00')
        )

        url = reverse('theater_list', args=[self.movie.id])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        schedules_context = response.context['schedules']
        self.assertIn(self.schedule, schedules_context)
        self.assertNotIn(past_schedule, schedules_context)
