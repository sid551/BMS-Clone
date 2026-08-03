"""
Reservation service layer.
Handles seat reservation, expiry, release, and seat-change logic.
All public functions raise ValueError with a human-readable message on failure.
"""
from django.utils import timezone
from datetime import timedelta
from .models import ShowSeat, ShowSchedule

RESERVATION_MINUTES = 2


def _expire_stale_reservations(show_schedule):
    """
    Release any reservations that have expired for a given schedule.
    Called before any read/write to ensure the seat map is fresh.
    """
    now = timezone.now()
    stale = ShowSeat.objects.filter(
        show_schedule=show_schedule,
        status='reserved',
        reserved_until__lt=now,
    )
    count = stale.count()
    if count:
        stale.update(status='available', reserved_by=None, reserved_until=None)
    return count


def reserve_seats(user, schedule_id, seat_ids):
    """
    Reserve a list of seats for a user.

    - Expires stale reservations first.
    - Releases any existing reservations by this user for this schedule.
    - Validates all requested seats are available.
    - Marks them reserved for RESERVATION_MINUTES.

    Returns list of ShowSeat objects that were reserved.
    Raises ValueError if any seat is unavailable.
    """
    schedule = ShowSchedule.objects.select_related('screen', 'theater', 'movie').get(pk=schedule_id)

    # Step 1: clean up expired reservations
    _expire_stale_reservations(schedule)

    # Step 2: release any seats this user already has reserved for this schedule
    release_user_reservations(user, schedule_id)

    if not seat_ids:
        raise ValueError('No seats selected.')

    # Step 3: fetch requested ShowSeat rows and validate
    show_seats = list(
        ShowSeat.objects.filter(
            show_schedule=schedule,
            seat_id__in=seat_ids,
        ).select_related('seat')
    )

    if len(show_seats) != len(seat_ids):
        raise ValueError('One or more seats do not belong to this schedule.')

    unavailable = [ss.seat.seat_number for ss in show_seats if ss.status != 'available']
    if unavailable:
        raise ValueError(f'Seats already taken: {", ".join(unavailable)}')

    # Step 4: reserve
    expiry = timezone.now() + timedelta(minutes=RESERVATION_MINUTES)
    seat_ids_to_update = [ss.pk for ss in show_seats]

    ShowSeat.objects.filter(pk__in=seat_ids_to_update).update(
        status='reserved',
        reserved_by=user,
        reserved_until=expiry,
    )

    # Return refreshed objects
    return list(ShowSeat.objects.filter(pk__in=seat_ids_to_update).select_related('seat'))


def release_user_reservations(user, schedule_id):
    """
    Release all seats currently reserved by this user for this schedule.
    Safe to call even if the user has no reservations.
    Returns the count of seats released.
    """
    released = ShowSeat.objects.filter(
        show_schedule_id=schedule_id,
        status='reserved',
        reserved_by=user,
    )
    count = released.count()
    if count:
        released.update(status='available', reserved_by=None, reserved_until=None)
    return count


def get_reservation_status(user, schedule_id):
    """
    Returns dict with:
      - seats: list of currently reserved seats for this user
      - seconds_remaining: time left (min across all reserved seats, or 0)
      - expired: True if any reservation has expired
    """
    _expire_stale_reservations_by_id(schedule_id)

    user_seats = ShowSeat.objects.filter(
        show_schedule_id=schedule_id,
        status='reserved',
        reserved_by=user,
    ).select_related('seat')

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
            'seconds_remaining': remaining,
        })

    return {
        'seats': seats_data,
        'seconds_remaining': min_remaining or 0,
        'expired': (min_remaining or 0) == 0,
    }


def confirm_reserved_seats(user, schedule_id):
    """
    Called after successful booking — marks all seats reserved by this user as booked.
    Returns count of seats confirmed.
    Raises ValueError if no reserved seats found.
    """
    reserved = ShowSeat.objects.filter(
        show_schedule_id=schedule_id,
        status='reserved',
        reserved_by=user,
    )
    if not reserved.exists():
        raise ValueError('No reserved seats found for this user and schedule.')

    count = reserved.count()
    reserved.update(status='booked', reserved_until=None)
    return count


def _expire_stale_reservations_by_id(schedule_id):
    """Same as _expire_stale_reservations but takes an ID."""
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
