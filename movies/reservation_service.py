"""
Reservation & Booking Service
==============================
All write operations use transaction.atomic() + select_for_update() to prevent
race conditions. Public functions raise ValueError on business rule violations.
"""
import uuid
from django.db import transaction
from django.utils import timezone
from datetime import timedelta
from .models import ShowSeat, ShowSchedule, Booking, BookingSeat

RESERVATION_MINUTES = 2


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _release_stale(schedule_id):
    """Bulk-release expired reservations for a schedule. No lock needed — safe update."""
    now = timezone.now()
    stale = ShowSeat.objects.filter(
        show_schedule_id=schedule_id,
        status='reserved',
        reserved_until__lt=now,
    )
    count = stale.count()
    if count:
        stale.update(status='available', reserved_by=None, reserved_until=None)
    return count


# ---------------------------------------------------------------------------
# Reserve seats (2-minute hold)
# ---------------------------------------------------------------------------

@transaction.atomic
def reserve_seats(user, schedule_id, seat_ids):
    """
    Reserve a list of seats for a user with row-level locking.

    Flow:
      1. Expire stale reservations.
      2. Release this user's existing reservations for the schedule.
      3. Lock the requested ShowSeat rows with select_for_update().
      4. Validate all are 'available'.
      5. Mark reserved with expiry timestamp.

    Returns list of reserved ShowSeat objects.
    Raises ValueError on any conflict.
    """
    if not seat_ids:
        raise ValueError('No seats selected.')

    # Step 1 — expire stale (outside lock, safe bulk update)
    _release_stale(schedule_id)

    # Step 2 — release this user's prior reservations
    _release_user_reservations_locked(user, schedule_id)

    # Step 3 — lock rows for the requested seats
    show_seats = list(
        ShowSeat.objects.select_for_update()
        .filter(show_schedule_id=schedule_id, seat_id__in=seat_ids)
        .select_related('seat')
        .order_by('seat__row', 'seat__number')
    )

    if len(show_seats) != len(set(seat_ids)):
        raise ValueError('One or more seats do not belong to this schedule.')

    # Step 4 — check all are available (after lock, so state is authoritative)
    unavailable = [ss.seat.seat_number for ss in show_seats if ss.status != 'available']
    if unavailable:
        raise ValueError(f'Seats no longer available: {", ".join(unavailable)}')

    # Step 5 — reserve
    expiry = timezone.now() + timedelta(minutes=RESERVATION_MINUTES)
    pks = [ss.pk for ss in show_seats]
    ShowSeat.objects.filter(pk__in=pks).update(
        status='reserved',
        reserved_by=user,
        reserved_until=expiry,
    )

    return list(
        ShowSeat.objects.filter(pk__in=pks).select_related('seat')
    )


def _release_user_reservations_locked(user, schedule_id):
    """Release existing reservations for this user (called inside atomic block)."""
    ShowSeat.objects.select_for_update().filter(
        show_schedule_id=schedule_id,
        status='reserved',
        reserved_by=user,
    ).update(status='available', reserved_by=None, reserved_until=None)


# ---------------------------------------------------------------------------
# Release seats
# ---------------------------------------------------------------------------

@transaction.atomic
def release_user_reservations(user, schedule_id):
    """
    Release all seats reserved by this user for a schedule.
    Returns count released.
    """
    seats = ShowSeat.objects.select_for_update().filter(
        show_schedule_id=schedule_id,
        status='reserved',
        reserved_by=user,
    )
    count = seats.count()
    if count:
        seats.update(status='available', reserved_by=None, reserved_until=None)
    return count


# ---------------------------------------------------------------------------
# Confirm booking (the critical atomic path)
# ---------------------------------------------------------------------------

@transaction.atomic
def confirm_booking(user, schedule_id):
    """
    Convert all seats reserved by this user into a confirmed Booking.

    Concurrency protection:
      - Locks ShowSeat rows with select_for_update().
      - Validates each is still reserved by this user and not expired.
      - Locks the ShowSchedule row and validates available_seats.
      - Creates Booking + BookingSeat records atomically.
      - Decrements schedule.available_seats.

    Returns the created Booking instance.
    Raises ValueError if any validation fails — entire transaction rolls back.
    """
    now = timezone.now()

    # Lock and fetch the user's reserved seats
    reserved_seats = list(
        ShowSeat.objects.select_for_update()
        .filter(
            show_schedule_id=schedule_id,
            status='reserved',
            reserved_by=user,
        )
        .select_related('seat')
        .order_by('seat__row', 'seat__number')
    )

    if not reserved_seats:
        raise ValueError('No reserved seats found. Your reservation may have expired.')

    # Validate none have expired
    expired = [ss.seat.seat_number for ss in reserved_seats if ss.reserved_until and ss.reserved_until < now]
    if expired:
        # Release expired ones
        ShowSeat.objects.filter(
            show_schedule_id=schedule_id,
            status='reserved',
            reserved_by=user,
            reserved_until__lt=now,
        ).update(status='available', reserved_by=None, reserved_until=None)
        raise ValueError(f'Reservation expired for seats: {", ".join(expired)}. Please select seats again.')

    # Lock the schedule row
    schedule = ShowSchedule.objects.select_for_update().select_related('movie', 'theater').get(pk=schedule_id)

    number_of_seats = len(reserved_seats)

    if schedule.available_seats < number_of_seats:
        raise ValueError(
            f'Not enough seats available on the schedule. Only {schedule.available_seats} left.'
        )

    # Calculate total price
    total_price = sum(
        ss.seat.calculate_price(schedule.price)
        for ss in reserved_seats
    )

    # Create Booking
    booking = Booking.objects.create(
        user=user,
        show_schedule=schedule,
        movie=schedule.movie,
        theater=schedule.theater,
        number_of_seats=number_of_seats,
        total_price=total_price,
        status='confirmed',
        booking_reference=f'BMS{uuid.uuid4().hex[:12].upper()}',
    )

    # Create BookingSeat records
    BookingSeat.objects.bulk_create([
        BookingSeat(
            booking=booking,
            show_schedule=schedule,
            seat=ss.seat,
            price=ss.seat.calculate_price(schedule.price),
        )
        for ss in reserved_seats
    ])

    # Mark ShowSeats as booked
    pks = [ss.pk for ss in reserved_seats]
    ShowSeat.objects.filter(pk__in=pks).update(
        status='booked',
        reserved_by=None,
        reserved_until=None,
    )

    # Decrement available seats on the schedule
    schedule.available_seats = max(0, schedule.available_seats - number_of_seats)
    schedule.save(update_fields=['available_seats'])

    return booking


# ---------------------------------------------------------------------------
# Status helpers
# ---------------------------------------------------------------------------

def get_reservation_status(user, schedule_id):
    """
    Returns current reservation state for a user on a schedule.
    Expires stale records first.
    """
    _release_stale(schedule_id)

    user_seats = (
        ShowSeat.objects
        .filter(show_schedule_id=schedule_id, status='reserved', reserved_by=user)
        .select_related('seat')
    )

    if not user_seats.exists():
        return {'seats': [], 'seconds_remaining': 0, 'expired': True}

    now = timezone.now()
    min_remaining = None
    seats_data = []

    for ss in user_seats:
        remaining = max(0, int((ss.reserved_until - now).total_seconds())) if ss.reserved_until else 0
        if min_remaining is None or remaining < min_remaining:
            min_remaining = remaining
        seats_data.append({
            'show_seat_id': ss.pk,
            'seat_id': ss.seat.pk,
            'seat_number': ss.seat.seat_number,
            'seat_type': ss.seat.get_seat_type_display(),
            'price': ss.seat.calculate_price(
                ShowSchedule.objects.get(pk=schedule_id).price
            ),
            'seconds_remaining': remaining,
        })

    return {
        'seats': seats_data,
        'seconds_remaining': min_remaining or 0,
        'expired': (min_remaining or 0) == 0,
    }


# Private alias used by seat_map_api
_expire_stale_reservations_by_id = _release_stale
