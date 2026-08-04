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
    Movie, Theater, Screen, Seat, ShowSchedule, ShowSeat, Booking, Payment
)
from movies.reservation_service import reserve_seats


@override_settings(
    RAZORPAY_KEY_ID='rzp_test_TGC8nqUhxruhge',
    RAZORPAY_KEY_SECRET='6bVYVwV5jpiMa0FRH7xT0QPV',
    RAZORPAY_WEBHOOK_SECRET='BmsWebHook'
)
class BookSeatsFlowTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='password123')
        self.client = Client()
        self.client.login(username='testuser', password='password123')

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

        self.schedule = ShowSchedule.objects.create(
            movie=self.movie,
            theater=self.theater,
            screen=self.screen,
            show_time=timezone.now() + timedelta(days=1),
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
        response = self.client.post(url, {'schedule_id': self.schedule.id})

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'movies/checkout.html')

        self.assertContains(response, 'Inception')
        self.assertContains(response, 'order_ui_test_123')
        self.assertContains(response, '600.00')
        self.assertContains(response, 'checkout.js')

        # Verify Payment record was created in database
        payment = Payment.objects.get(gateway_order_id='order_ui_test_123')
        self.assertEqual(payment.status, 'pending')
        self.assertEqual(payment.amount, Decimal('600.00'))
