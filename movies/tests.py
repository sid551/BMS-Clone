from datetime import timedelta
from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.urls import reverse

from .models import (
    Genre, Language, CastMember, Movie, MovieImage,
    Theater, Screen, Seat, ShowSchedule, Booking, BookingSeat, Review, ReportedReview
)
from .templatetags.movie_tags import youtube_embed_id, format_duration
from .recommendations import get_similar_movies, get_trending_movies, get_recently_released


class MovieManagementTestCase(TestCase):
    def setUp(self):
        # Users
        self.normal_user = User.objects.create_user(username='john', password='password123')
        self.other_user = User.objects.create_user(username='jane', password='password123')
        self.staff_user = User.objects.create_superuser(username='admin', password='adminpassword')

        # Genres & Languages
        self.action = Genre.objects.create(name='Action')
        self.sci_fi = Genre.objects.create(name='Sci-Fi')
        self.drama = Genre.objects.create(name='Drama')

        self.english = Language.objects.create(name='English')
        self.hindi = Language.objects.create(name='Hindi')

        # Cast
        self.actor = CastMember.objects.create(name='Christopher Nolan', role='director')

        # Movies
        self.movie1 = Movie.objects.create(
            title='Inception',
            description='A thief who steals corporate secrets through the use of dream-sharing technology.',
            duration_minutes=148,
            release_date=timezone.now().date() - timedelta(days=30),
            age_certification='U/A 13+',
            trailer_url='https://www.youtube.com/watch?v=YoHD9XEInc0',
            status='now_showing'
        )
        self.movie1.genres.add(self.action, self.sci_fi)
        self.movie1.languages.add(self.english)
        self.movie1.cast.add(self.actor)

        self.movie2 = Movie.objects.create(
            title='Interstellar',
            description='A team of explorers travel through a wormhole in space.',
            duration_minutes=169,
            release_date=timezone.now().date() - timedelta(days=60),
            age_certification='U/A 13+',
            trailer_url='https://youtu.be/zSWdZVtXT7E',
            status='now_showing'
        )
        self.movie2.genres.add(self.sci_fi, self.drama)
        self.movie2.languages.add(self.english)

        # Theater & Schedules
        self.theater = Theater.objects.create(name='PVR Cinema', location='Downtown', total_seats=100)

        # Past schedule
        self.past_schedule = ShowSchedule.objects.create(
            movie=self.movie1,
            theater=self.theater,
            show_time=timezone.now() - timedelta(hours=5),
            price=250.00,
            available_seats=100
        )

        # Future schedule
        self.future_schedule = ShowSchedule.objects.create(
            movie=self.movie1,
            theater=self.theater,
            show_time=timezone.now() + timedelta(days=2),
            price=250.00,
            available_seats=100
        )

        self.client = Client()

    def test_youtube_embed_id_filter(self):
        url1 = 'https://www.youtube.com/watch?v=YoHD9XEInc0'
        url2 = 'https://youtu.be/zSWdZVtXT7E?t=30'
        url3 = 'https://www.youtube.com/shorts/abcdefghijk'
        url4 = 'https://m.youtube.com/watch?v=12345678901'

        self.assertIn('YoHD9XEInc0', youtube_embed_id(url1))
        self.assertIn('zSWdZVtXT7E', youtube_embed_id(url2))
        self.assertIn('abcdefghijk', youtube_embed_id(url3))
        self.assertIn('12345678901', youtube_embed_id(url4))

    def test_format_duration(self):
        self.assertEqual(format_duration(148), '2h 28m')
        self.assertEqual(format_duration(60), '1h')
        self.assertEqual(format_duration(45), '45m')
        self.assertEqual(self.movie1.duration_formatted, '2h 28m')

    def test_booking_and_review_restrictions(self):
        # 1. Unbooked user cannot review
        review = Review(movie=self.movie1, user=self.normal_user, rating=5, title='Great', text='Awesome movie')
        with self.assertRaises(ValidationError):
            review.full_clean()

        # 2. Book future show
        future_booking = Booking.objects.create(
            user=self.normal_user,
            show_schedule=self.future_schedule,
            number_of_seats=2,
            status='confirmed'
        )
        with self.assertRaises(ValidationError):
            review.full_clean()

        # 3. Book past show
        past_booking = Booking.objects.create(
            user=self.normal_user,
            show_schedule=self.past_schedule,
            number_of_seats=2,
            status='confirmed'
        )
        # Should now pass clean validation
        review.full_clean()
        review.save()

        self.assertTrue(review.is_verified)
        self.assertEqual(self.movie1.rating, 5.0)
        self.assertEqual(self.movie1.review_count, 1)

    def test_average_rating_recalculation(self):
        # Setup past bookings
        Booking.objects.create(
            user=self.normal_user,
            show_schedule=self.past_schedule,
            number_of_seats=1,
            status='confirmed'
        )
        Booking.objects.create(
            user=self.other_user,
            show_schedule=self.past_schedule,
            number_of_seats=1,
            status='confirmed'
        )

        r1 = Review.objects.create(movie=self.movie1, user=self.normal_user, rating=4, title='Good', text='Enjoyed it')
        self.movie1.refresh_from_db()
        self.assertEqual(self.movie1.rating, 4.0)

        r2 = Review.objects.create(movie=self.movie1, user=self.other_user, rating=2, title='Okay', text='Average')
        self.movie1.refresh_from_db()
        # Avg of 4 and 2 is 3.0
        self.assertEqual(self.movie1.rating, 3.0)
        self.assertEqual(self.movie1.review_count, 2)

        # Deactivate r2
        r2.is_active = False
        r2.save()
        self.movie1.refresh_from_db()
        self.assertEqual(self.movie1.rating, 4.0)
        self.assertEqual(self.movie1.review_count, 1)

    def test_report_review_flow(self):
        Booking.objects.create(
            user=self.normal_user,
            show_schedule=self.past_schedule,
            number_of_seats=1,
            status='confirmed'
        )
        review = Review.objects.create(movie=self.movie1, user=self.normal_user, rating=5, title='Loved it', text='Nice')

        # Report review by other user
        report = ReportedReview.objects.create(
            review=review,
            reported_by=self.other_user,
            reason='spam',
            comments='Looks like promo'
        )
        self.assertEqual(report.status, 'pending')

        # Admin resolves report by hiding review
        self.client.login(username='admin', password='adminpassword')
        response = self.client.post(reverse('admin_resolve_report', args=[report.id]), {'action': 'hide_review'})
        self.assertEqual(response.status_code, 302)

        review.refresh_from_db()
        report.refresh_from_db()
        self.assertFalse(review.is_active)
        self.assertEqual(report.status, 'resolved')

    def test_recommendations(self):
        similar = list(get_similar_movies(self.movie1))
        self.assertIn(self.movie2, similar)

        trending = list(get_trending_movies())
        self.assertGreaterEqual(len(trending), 1)

        recent = list(get_recently_released())
        self.assertEqual(recent[0], self.movie1)

    def test_custom_admin_permissions(self):
        # Normal user access denied
        self.client.login(username='john', password='password123')
        resp = self.client.get(reverse('admin_dashboard'))
        self.assertEqual(resp.status_code, 302)

        # Staff user access granted
        self.client.login(username='admin', password='adminpassword')
        resp = self.client.get(reverse('admin_dashboard'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Movie Management Admin Panel')

    def test_screen_and_seat_management_system(self):
        from .models import Screen, Seat, BookingSeat

        # Create Screen
        screen = Screen.objects.create(
            theater=self.theater,
            name='Screen 1 - IMAX',
            screen_type='IMAX_3D',
            total_rows=8,
            seats_per_row=10
        )
        self.assertEqual(screen.total_seats, 80)

        # Auto-generate seat layout
        screen.generate_seats()
        self.assertEqual(screen.seats.count(), 80)

        # Verify Tiers
        recliner_seats = screen.seats.filter(seat_type='recliner')
        premium_seats = screen.seats.filter(seat_type='premium')
        regular_seats = screen.seats.filter(seat_type='regular')

        self.assertGreater(recliner_seats.count(), 0)
        self.assertGreater(premium_seats.count(), 0)
        self.assertGreater(regular_seats.count(), 0)

        # Verify Pricing Multiplier
        sample_recliner = recliner_seats.first()
        self.assertEqual(sample_recliner.calculate_price(200.00), 300.00)

        # Test booking seats via view
        schedule = ShowSchedule.objects.create(
            movie=self.movie1,
            theater=self.theater,
            screen=screen,
            show_time=timezone.now() + timedelta(days=3),
            price=200.00,
            available_seats=80
        )

        self.client.login(username='john', password='password123')
        seat_ids = [sample_recliner.id]
        resp = self.client.post(
            f"{reverse('book_seats', args=[self.theater.id])}?schedule_id={schedule.id}",
            {'seats': seat_ids}
        )
        self.assertEqual(resp.status_code, 302)

        schedule.refresh_from_db()
        self.assertEqual(schedule.available_seats, 79)
        self.assertTrue(BookingSeat.objects.filter(show_schedule=schedule, seat=sample_recliner).exists())

        # Test Staff Screen Admin view
        self.client.login(username='admin', password='adminpassword')
        map_resp = self.client.get(reverse('admin_screen_seat_map', args=[screen.id]))
        self.assertEqual(map_resp.status_code, 200)
        self.assertContains(map_resp, 'Screen 1 - IMAX')

    def test_admin_layman_seat_management(self):
        from .models import Screen, Seat, BookingSeat

        screen = Screen.objects.create(
            theater=self.theater,
            name='Screen 2 - VIP',
            screen_type='4DX',
            total_rows=5,
            seats_per_row=6
        )
        screen.generate_seats()

        self.client.login(username='admin', password='adminpassword')

        # 1. Access admin manage seats view
        url = f"{reverse('admin_manage_seats')}?theater_id={self.theater.id}&screen_id={screen.id}"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Layout Management')


        # 2. Single seat status change -> mark booked
        sample_seat = screen.seats.first()
        self.client.post(reverse('admin_update_seat_status'), {
            'action_type': 'single_seat',
            'screen_id': screen.id,
            'seat_id': sample_seat.id,
            'status': 'booked'
        })
        sample_seat.refresh_from_db()
        self.assertTrue(sample_seat.is_booked)

        # 3. Single seat status change -> mark available
        self.client.post(reverse('admin_update_seat_status'), {
            'action_type': 'single_seat',
            'screen_id': screen.id,
            'seat_id': sample_seat.id,
            'status': 'available'
        })
        sample_seat.refresh_from_db()
        self.assertFalse(sample_seat.is_booked)
        self.assertTrue(sample_seat.is_active)

        # 4. Single seat status change -> mark maintenance
        self.client.post(reverse('admin_update_seat_status'), {
            'action_type': 'single_seat',
            'screen_id': screen.id,
            'seat_id': sample_seat.id,
            'status': 'maintenance'
        })
        sample_seat.refresh_from_db()
        self.assertFalse(sample_seat.is_active)

        # 5. Bulk Row action -> mark Row A as booked
        self.client.post(reverse('admin_update_seat_status'), {
            'action_type': 'bulk_row',
            'screen_id': screen.id,
            'row_letter': 'A',
            'row_status': 'booked'
        })
        row_a_seats = screen.seats.filter(row='A')
        for s in row_a_seats:
            self.assertTrue(s.is_booked)

        # 6. Reset screen seats
        self.client.post(reverse('admin_update_seat_status'), {
            'action_type': 'reset_screen',
            'screen_id': screen.id
        })
        for s in screen.seats.all():
            self.assertFalse(s.is_booked)
            self.assertTrue(s.is_active)

    def test_interconnected_admin_and_auto_sync(self):
        from .models import Screen, ShowSchedule, Booking

        # 1. Test Screen capacity & Theater total_seats auto-sync
        screen = Screen.objects.create(
            theater=self.theater,
            name='Screen 3 - AutoSync',
            screen_type='DOLBY',
            total_rows=10,
            seats_per_row=10
        )
        # Verify auto generated 100 seats and updated theater capacity
        self.assertEqual(screen.seats.count(), 100)
        self.theater.refresh_from_db()
        self.assertEqual(self.theater.total_seats, 100)

        # 2. Test ShowSchedule auto-populate available_seats
        schedule = ShowSchedule.objects.create(
            movie=self.movie1,
            theater=self.theater,
            screen=screen,
            show_time=timezone.now() + timedelta(days=5),
            price=250.00
        )
        self.assertEqual(schedule.available_seats, 100)

        # 3. Test Bulk Schedule Generator view
        self.client.login(username='admin', password='adminpassword')
        bulk_resp = self.client.post(reverse('admin_bulk_schedule_add'), {
            'movie_id': self.movie1.id,
            'screen_id': screen.id,
            'start_date': (timezone.now() + timedelta(days=6)).strftime('%Y-%m-%d'),
            'end_date': (timezone.now() + timedelta(days=7)).strftime('%Y-%m-%d'),
            'time_slots': '10:00, 18:00',
            'price': 300.00
        })
        self.assertEqual(bulk_resp.status_code, 302)

        # 4. Test Bookings Admin Management view
        booking = Booking.objects.create(
            user=self.normal_user,


            show_schedule=schedule,
            number_of_seats=2,
            total_price=500.00,
            status='pending',
            movie=self.movie1,
            theater=self.theater
        )
        bookings_url = reverse('admin_manage_bookings')
        resp = self.client.get(bookings_url)
        self.assertEqual(resp.status_code, 200)

        # Test Confirm Booking Action
        self.client.post(reverse('admin_booking_action', args=[booking.id]), {'action': 'confirm'})
        booking.refresh_from_db()
        self.assertEqual(booking.status, 'confirmed')

    def test_blank_available_seats_auto_sync(self):
        from .models import Screen, ShowSchedule

        screen = Screen.objects.create(
            theater=self.theater,
            name='Screen Blank Test',
            screen_type='2D',
            total_rows=5,
            seats_per_row=10
        )
        screen.generate_seats()
        self.assertEqual(screen.total_seats, 50)

        self.client.login(username='admin', password='adminpassword')

        # Test POSTing schedule with available_seats left BLANK ('')
        post_data = {
            'movie': self.movie1.id,
            'theater': self.theater.id,
            'screen': screen.id,
            'show_time': (timezone.now() + timedelta(days=10)).strftime('%Y-%m-%dT%H:%M'),
            'price': '250.00',
            'available_seats': ''  # LEFT BLANK BY USER
        }
        resp = self.client.post(reverse('admin_schedule_add'), post_data)
        self.assertEqual(resp.status_code, 302)

        created_schedule = ShowSchedule.objects.filter(screen=screen).first()
        self.assertIsNotNone(created_schedule)
        self.assertEqual(created_schedule.available_seats, 50)

    def test_admin_toggle_seat_ajax(self):
        from .models import Screen, Seat, ShowSchedule

        screen = Screen.objects.create(
            theater=self.theater,
            name='Screen GUI Test',
            screen_type='3D',
            total_rows=4,
            seats_per_row=5
        )
        screen.generate_seats()
        schedule = ShowSchedule.objects.create(
            movie=self.movie1,
            theater=self.theater,
            screen=screen,
            show_time=timezone.now() + timedelta(days=12),
            price=200.00
        )

        self.client.login(username='admin', password='adminpassword')
        sample_seat = screen.seats.first()

        # Toggle Available -> Booked via GUI AJAX
        resp = self.client.post(reverse('admin_toggle_seat_ajax'), {
            'seat_id': sample_seat.id,
            'screen_id': screen.id,
            'schedule_id': schedule.id
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['new_status'], 'booked')
        self.assertEqual(data['schedule_available_seats'], 19)

        # Toggle Booked -> Available via GUI AJAX
        resp2 = self.client.post(reverse('admin_toggle_seat_ajax'), {
            'seat_id': sample_seat.id,
            'screen_id': screen.id,
            'schedule_id': schedule.id
        })
        self.assertEqual(resp2.status_code, 200)
        data2 = resp2.json()
        self.assertTrue(data2['success'])
        self.assertEqual(data2['new_status'], 'available')
        self.assertEqual(data2['schedule_available_seats'], 20)


class DjangoAdminFeatureParityTestCase(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(username='superadmin', email='admin@test.com', password='password123')
        self.client.login(username='superadmin', password='password123')

        self.movie = Movie.objects.create(
            title='Admin Parity Movie',
            duration_minutes=120,
            release_date=timezone.now().date(),
            status='now_showing'
        )
        self.theater = Theater.objects.create(name='Admin Cinema', location='City Center')
        self.screen = Screen.objects.create(theater=self.theater, name='Screen 1', screen_type='2D', total_rows=5, seats_per_row=10)

    def test_django_admin_bulk_schedule_add(self):
        url = reverse('admin:movies_showschedule_bulk_add')
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Bulk Add Show Schedules')

        post_data = {
            'movie_id': self.movie.id,
            'screen_id': self.screen.id,
            'start_date': timezone.now().date().strftime('%Y-%m-%d'),
            'end_date': (timezone.now().date() + timedelta(days=1)).strftime('%Y-%m-%d'),
            'time_slots': '10:00, 16:00',
            'price': 250.00
        }
        post_resp = self.client.post(url, post_data)
        self.assertEqual(post_resp.status_code, 302)
        self.assertEqual(ShowSchedule.objects.filter(movie=self.movie).count(), 4)

    def test_django_admin_interactive_seat_matrix(self):
        url = reverse('admin:movies_seat_matrix')
        resp = self.client.get(f"{url}?theater_id={self.theater.id}&screen_id={self.screen.id}")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Interactive Seat Matrix')

    def test_screen_save_model_auto_seats(self):
        new_screen = Screen.objects.create(theater=self.theater, name='Screen 2', screen_type='IMAX', total_rows=2, seats_per_row=5)
        # Calling save_model logic via admin or generate_seats
        new_screen.generate_seats()
        self.assertEqual(Seat.objects.filter(screen=new_screen).count(), 10)






