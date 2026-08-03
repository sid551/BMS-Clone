import threading
import time
from datetime import timedelta
from django.test import TransactionTestCase
from django.contrib.auth.models import User
from django.utils import timezone
from django.db import connection, transaction

from movies.models import (
    Movie, Theater, Screen, Seat, ShowSchedule, ShowSeat, Booking, BookingSeat
)
from movies.reservation_service import (
    reserve_seats, confirm_booking, release_user_reservations, get_reservation_status, _release_stale
)


class SmartSeatReservationTestSuite(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        # Create users
        self.user_a = User.objects.create_user(username='user_a', password='password123')
        self.user_b = User.objects.create_user(username='user_b', password='password123')
        self.user_c = User.objects.create_user(username='user_c', password='password123')

        # Create movie
        self.movie = Movie.objects.create(
            title='Inception - Smart Seat Test',
            duration_minutes=148,
            release_date=timezone.now().date(),
            status='now_showing'
        )

        # Create theater & screen (8 rows x 10 seats = 80 seats)
        self.theater = Theater.objects.create(name='PVR Cyber Hub', location='Gurugram')
        self.screen = Screen.objects.create(
            theater=self.theater,
            name='AUDI 1',
            screen_type='IMAX_3D',
            total_rows=8,
            seats_per_row=10
        )
        self.screen.generate_seats()

        # Create show schedule
        self.schedule = ShowSchedule.objects.create(
            movie=self.movie,
            theater=self.theater,
            screen=self.screen,
            show_time=timezone.now() + timedelta(days=1),
            price=300.00
        )

        # Second schedule for schedule isolation tests
        self.schedule_2 = ShowSchedule.objects.create(
            movie=self.movie,
            theater=self.theater,
            screen=self.screen,
            show_time=timezone.now() + timedelta(days=2),
            price=350.00
        )

    def test_01_seat_generation(self):
        """Test 1 – Seat Generation: Auto creation, uniqueness, initial Available status"""
        total_seats = self.screen.total_seats
        self.assertEqual(total_seats, 80)

        show_seats = ShowSeat.objects.filter(show_schedule=self.schedule)
        self.assertEqual(show_seats.count(), 80)

        seat_numbers = [ss.seat.seat_number for ss in show_seats]
        self.assertEqual(len(seat_numbers), len(set(seat_numbers)), "Duplicate seat numbers found!")

        statuses = set(ss.status for ss in show_seats)
        self.assertEqual(statuses, {'available'}, "Not all seats are initially Available!")

    def test_02_live_seat_availability(self):
        """Test 2 – Live Seat Availability: Status reflection & distinct visual data"""
        seats = list(Seat.objects.filter(screen=self.screen).order_by('id')[:3])
        
        # Mark 1 reserved by User A, 1 booked, 1 available
        reserve_seats(self.user_a, self.schedule.id, [seats[0].id])
        
        # Manually set seats[1] to booked for layout verification
        ss_booked = ShowSeat.objects.get(show_schedule=self.schedule, seat=seats[1])
        ss_booked.status = 'booked'
        ss_booked.save()

        show_seats = ShowSeat.objects.filter(show_schedule=self.schedule)
        available_count = show_seats.filter(status='available').count()
        reserved_count = show_seats.filter(status='reserved').count()
        booked_count = show_seats.filter(status='booked').count()

        self.assertEqual(reserved_count, 1)
        self.assertEqual(booked_count, 1)
        self.assertEqual(available_count, 78)

    def test_03_temporary_reservation(self):
        """Test 3 – Temporary Reservation: 2-min hold, immediate status change, isolation"""
        seats = list(Seat.objects.filter(screen=self.screen).order_by('id')[:2])
        seat_ids = [s.id for s in seats]

        reserved = reserve_seats(self.user_a, self.schedule.id, seat_ids)
        self.assertEqual(len(reserved), 2)

        for ss in reserved:
            self.assertEqual(ss.status, 'reserved')
            self.assertEqual(ss.reserved_by, self.user_a)
            # Expiry should be around 2 minutes from now
            diff = (ss.reserved_until - timezone.now()).total_seconds()
            self.assertAlmostEqual(diff, 120, delta=5)

        # User B attempts to reserve same seats -> should fail
        with self.assertRaises(ValueError) as ctx:
            reserve_seats(self.user_b, self.schedule.id, seat_ids)
        self.assertIn("Seats no longer available", str(ctx.exception))

    def test_04_reservation_expiration(self):
        """Test 4 – Reservation Expiration: Expired seats return to Available"""
        seats = list(Seat.objects.filter(screen=self.screen).order_by('id')[:2])
        seat_ids = [s.id for s in seats]

        reserve_seats(self.user_a, self.schedule.id, seat_ids)

        # Simulate expiration by moving reserved_until to past
        ShowSeat.objects.filter(show_schedule=self.schedule, seat_id__in=seat_ids).update(
            reserved_until=timezone.now() - timedelta(seconds=10)
        )

        # Release stale reservations
        count = _release_stale(self.schedule.id)
        self.assertEqual(count, 2)

        for ss in ShowSeat.objects.filter(show_schedule=self.schedule, seat_id__in=seat_ids):
            self.assertEqual(ss.status, 'available')
            self.assertIsNone(ss.reserved_by)
            self.assertIsNone(ss.reserved_until)

    def test_05_modify_reserved_seats(self):
        """Test 5 – Modify Reserved Seats: Add/remove seats updates reservation & timer"""
        seats = list(Seat.objects.filter(screen=self.screen).order_by('id')[:4])

        # Step 1: Reserve 3 seats (seats[0], seats[1], seats[2])
        reserve_seats(self.user_a, self.schedule.id, [seats[0].id, seats[1].id, seats[2].id])

        # Step 2: Remove seats[2] and add seats[3] -> reserve (seats[0], seats[1], seats[3])
        reserve_seats(self.user_a, self.schedule.id, [seats[0].id, seats[1].id, seats[3].id])

        # Verify seats[2] is now Available
        ss_2 = ShowSeat.objects.get(show_schedule=self.schedule, seat=seats[2])
        self.assertEqual(ss_2.status, 'available')
        self.assertIsNone(ss_2.reserved_by)

        # Verify seats[0], seats[1], seats[3] are Reserved
        for s in [seats[0], seats[1], seats[3]]:
            ss = ShowSeat.objects.get(show_schedule=self.schedule, seat=s)
            self.assertEqual(ss.status, 'reserved')
            self.assertEqual(ss.reserved_by, self.user_a)

    def test_06_successful_booking(self):
        """Test 6 – Successful Booking: Reserve -> Confirm -> Permanent Booked status"""
        seats = list(Seat.objects.filter(screen=self.screen).order_by('id')[:2])
        seat_ids = [s.id for s in seats]

        reserve_seats(self.user_a, self.schedule.id, seat_ids)
        booking = confirm_booking(self.user_a, self.schedule.id)

        self.assertIsNotNone(booking)
        self.assertEqual(booking.status, 'confirmed')
        self.assertEqual(booking.number_of_seats, 2)
        self.assertEqual(booking.booked_seats.count(), 2)

        # Verify ShowSeat status is permanently booked
        for ss in ShowSeat.objects.filter(show_schedule=self.schedule, seat_id__in=seat_ids):
            self.assertEqual(ss.status, 'booked')
            self.assertIsNone(ss.reserved_by)

        # Confirm seats cannot be reserved again
        with self.assertRaises(ValueError):
            reserve_seats(self.user_b, self.schedule.id, seat_ids)

    def test_07_concurrent_reservation(self):
        """Test 7 – Concurrent Reservation: Multi-thread race for same seats"""
        seats = list(Seat.objects.filter(screen=self.screen).order_by('id')[:2])
        seat_ids = [s.id for s in seats]

        results = {}

        def attempt_reservation(user, key):
            connection.close()
            try:
                res = reserve_seats(user, self.schedule.id, seat_ids)
                results[key] = ('SUCCESS', res)
            except Exception as e:
                results[key] = ('ERROR', str(e))

        t1 = threading.Thread(target=attempt_reservation, args=(self.user_a, 'user_a'))
        t2 = threading.Thread(target=attempt_reservation, args=(self.user_b, 'user_b'))

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        successes = [v for v in results.values() if v[0] == 'SUCCESS']
        errors = [v for v in results.values() if v[0] == 'ERROR']

        # At most 1 user should succeed
        self.assertLessEqual(len(successes), 1, "More than 1 user reserved the same seats!")
        self.assertGreaterEqual(len(errors), 1, "Expected concurrency conflict error!")
        
        # Verify no duplicate reservations exist in database
        reserved_count = ShowSeat.objects.filter(
            show_schedule=self.schedule, seat_id__in=seat_ids, status='reserved'
        ).count()
        self.assertIn(reserved_count, [0, 2])

    def test_08_concurrent_booking(self):
        """Test 8 – Concurrent Booking: Prevent double confirmation / race conditions"""
        seats = list(Seat.objects.filter(screen=self.screen).order_by('id')[:2])
        seat_ids = [s.id for s in seats]

        reserve_seats(self.user_a, self.schedule.id, seat_ids)

        results = []

        def attempt_confirm():
            connection.close()
            try:
                b = confirm_booking(self.user_a, self.schedule.id)
                results.append(('SUCCESS', b.id))
            except Exception as e:
                results.append(('ERROR', str(e)))

        t1 = threading.Thread(target=attempt_confirm)
        t2 = threading.Thread(target=attempt_confirm)

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        successes = [r for r in results if r[0] == 'SUCCESS']
        errors = [r for r in results if r[0] == 'ERROR']

        # At most 1 booking should succeed
        self.assertLessEqual(len(successes), 1, f"More than 1 booking succeeded: {results}")
        self.assertGreaterEqual(len(errors), 1, "Expected concurrency conflict error!")

        # Check total bookings in DB is at most 1 (no double booking)
        booking_count = Booking.objects.filter(user=self.user_a, show_schedule=self.schedule).count()
        self.assertLessEqual(booking_count, 1)

    def test_09_atomic_transactions(self):
        """Test 9 – Atomic Transactions: Expiration during checkout rolls back entire booking"""
        seats = list(Seat.objects.filter(screen=self.screen).order_by('id')[:3])
        seat_ids = [s.id for s in seats]

        reserve_seats(self.user_a, self.schedule.id, seat_ids)

        # Expire 1 seat out of 3
        ShowSeat.objects.filter(show_schedule=self.schedule, seat=seats[0]).update(
            reserved_until=timezone.now() - timedelta(seconds=5)
        )

        with self.assertRaises(ValueError) as ctx:
            confirm_booking(self.user_a, self.schedule.id)

        self.assertIn("Reservation expired for seats", str(ctx.exception))

        # Verify NO bookings created
        self.assertEqual(Booking.objects.filter(user=self.user_a).count(), 0)

        # After stale release, expired seat returns to Available
        _release_stale(self.schedule.id)
        ss_0 = ShowSeat.objects.get(show_schedule=self.schedule, seat=seats[0])
        self.assertEqual(ss_0.status, 'available')

    def test_10_invalid_operations(self):
        """Test 10 – Invalid Operations: Comprehensive validation checks"""
        seats = list(Seat.objects.filter(screen=self.screen).order_by('id')[:4])

        # 1. Reserving already reserved seats
        reserve_seats(self.user_a, self.schedule.id, [seats[0].id])
        with self.assertRaises(ValueError) as ctx1:
            reserve_seats(self.user_b, self.schedule.id, [seats[0].id])
        self.assertIn("Seats no longer available", str(ctx1.exception))

        # 2. Reserving already booked seats
        confirm_booking(self.user_a, self.schedule.id)
        with self.assertRaises(ValueError) as ctx2:
            reserve_seats(self.user_b, self.schedule.id, [seats[0].id])
        self.assertIn("Seats no longer available", str(ctx2.exception))

        # 3. Booking without a valid reservation
        with self.assertRaises(ValueError) as ctx3:
            confirm_booking(self.user_b, self.schedule.id)
        self.assertIn("No reserved seats found", str(ctx3.exception))

        # 4. Booking after reservation expiry
        reserve_seats(self.user_b, self.schedule.id, [seats[1].id])
        ShowSeat.objects.filter(show_schedule=self.schedule, seat=seats[1]).update(
            reserved_until=timezone.now() - timedelta(seconds=10)
        )
        with self.assertRaises(ValueError) as ctx4:
            confirm_booking(self.user_b, self.schedule.id)
        self.assertTrue(
            "Reservation expired" in str(ctx4.exception) or "expired" in str(ctx4.exception)
        )

        # 5. Reserving seats from another show schedule / screen invalid seat IDs
        with self.assertRaises(ValueError) as ctx5:
            reserve_seats(self.user_b, self.schedule.id, [999999])
        self.assertIn("do not belong to this schedule", str(ctx5.exception))

    def test_11_stress_test(self):
        """Test 11 – Stress Test: Multiple users reserving non-overlapping seats"""
        all_seats = list(Seat.objects.filter(screen=self.screen).order_by('id')[:20])
        users = [
            User.objects.create_user(username=f'stress_user_{i}', password='password123')
            for i in range(10)
        ]

        errors = []
        successful_bookings = 0

        for i, user in enumerate(users):
            pair_seats = [all_seats[i*2].id, all_seats[i*2 + 1].id]
            try:
                reserve_seats(user, self.schedule.id, pair_seats)
                confirm_booking(user, self.schedule.id)
                successful_bookings += 1
            except Exception as e:
                errors.append((user.username, str(e)))

        self.assertEqual(len(errors), 0, f"Stress test encountered errors: {errors}")
        self.assertEqual(successful_bookings, 10)

        # Check DB state
        booked_show_seats = ShowSeat.objects.filter(show_schedule=self.schedule, status='booked').count()
        self.assertEqual(booked_show_seats, 20)

        total_bookings = Booking.objects.filter(show_schedule=self.schedule).count()
        self.assertEqual(total_bookings, 10)

        booked_seat_links = BookingSeat.objects.filter(show_schedule=self.schedule).count()
        self.assertEqual(booked_seat_links, 20)

    def test_12_database_validation(self):
        """Test 12 – Database Validation: Integrity of bookings, seats, expirations, matching states"""
        seats = list(Seat.objects.filter(screen=self.screen).order_by('id')[:3])
        seat_ids = [s.id for s in seats]

        reserve_seats(self.user_a, self.schedule.id, seat_ids)
        booking = confirm_booking(self.user_a, self.schedule.id)

        # 1. Every booked seat belongs to exactly one booking
        for s in seats:
            booking_seats = BookingSeat.objects.filter(show_schedule=self.schedule, seat=s)
            self.assertEqual(booking_seats.count(), 1)
            self.assertEqual(booking_seats.first().booking, booking)

        # 2. ShowSeat status matches booking/reservation status
        for s in seats:
            ss = ShowSeat.objects.get(show_schedule=self.schedule, seat=s)
            self.assertEqual(ss.status, 'booked')

        # 3. Reserved seat expiration integrity
        seat_b = Seat.objects.filter(screen=self.screen).order_by('id')[5]
        reserved_ss = reserve_seats(self.user_b, self.schedule.id, [seat_b.id])[0]
        self.assertIsNotNone(reserved_ss.reserved_until)
        self.assertGreater(reserved_ss.reserved_until, timezone.now())

        # 4. Schedule available seats count consistency
        self.schedule.refresh_from_db()
        expected_confirmed_available = 80 - 3
        self.assertEqual(self.schedule.available_seats, expected_confirmed_available)

        live_available_count = ShowSeat.objects.filter(show_schedule=self.schedule, status='available').count()
        self.assertEqual(live_available_count, 80 - 3 - 1)
