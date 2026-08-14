"""
Cancellation & Refund Service — PVR Cinemas (BookMyShow / Paytm) Standard
========================================================================
Policy Rules:
  1. Cutoff: Cancellations permitted up to 20 minutes prior to showtime.
  2. Refund Tiers:
     - 2 hours or more prior to showtime: 75% refund of base ticket price.
     - Between 20 minutes and 2 hours prior: 50% refund of base ticket price.
     - Less than 20 minutes prior: Cancellation not allowed (0% refund).
"""

from decimal import Decimal, ROUND_HALF_UP
from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from django.core.exceptions import ValidationError
from .models import Booking, Payment, ShowSeat, ShowSchedule


def calculate_pvr_cancellation_policy(show_time, total_price: Decimal, is_staff: bool = False) -> tuple:
    """
    Calculates eligibility and refund percentage based on PVR Cinemas standard.

    Returns:
        (is_eligible, refund_amount, refund_percentage, policy_description)
    """
    if not total_price or total_price <= 0:
        return True, Decimal('0.00'), 0, "No charge to refund."

    if not show_time:
        # Fallback if no show schedule attached
        amount = (total_price * Decimal('0.75')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        return True, amount, 75, "Standard 75% refund applied."

    now = timezone.now()
    time_diff = show_time - now
    minutes_remaining = time_diff.total_seconds() / 60.0

    if minutes_remaining < 20 and not is_staff:
        return False, Decimal('0.00'), 0, "Cancellations are only permitted up to 20 minutes prior to showtime."

    if minutes_remaining >= 120 or is_staff:
        # 2 hours or more prior -> 75% refund
        pct = 75
        amount = (total_price * Decimal('0.75')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        desc = "Cancelled 2+ hours prior to showtime (75% refund tier)."
    else:
        # Between 20 minutes and 2 hours prior -> 50% refund
        pct = 50
        amount = (total_price * Decimal('0.50')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        desc = "Cancelled between 20 mins and 2 hours prior to showtime (50% refund tier)."

    return True, amount, pct, desc


@transaction.atomic
def cancel_booking_and_process_refund(booking: Booking, cancelled_by=None, reason='User requested cancellation') -> dict:
    """
    Cancel a confirmed or pending booking, release seats back to available status,
    and process partial/full refund based on PVR Cinemas policy.
    """
    if not booking:
        raise ValidationError('Booking record not found.')

    if booking.status == 'cancelled':
        raise ValidationError('This booking has already been cancelled.')

    if booking.status not in ('confirmed', 'pending'):
        raise ValidationError(f'Booking with status "{booking.status}" cannot be cancelled.')

    is_staff_user = bool(cancelled_by and (cancelled_by.is_staff or cancelled_by.is_superuser))
    show_time = booking.show_schedule.show_time if booking.show_schedule else None

    # Calculate eligibility and tier refund based on PVR standard
    is_eligible, refund_amount, pct, policy_desc = calculate_pvr_cancellation_policy(
        show_time=show_time,
        total_price=booking.total_price,
        is_staff=is_staff_user
    )

    if not is_eligible:
        raise ValidationError(policy_desc)

    schedule = booking.show_schedule

    # Step 1: Release all seats attached to this booking
    booked_seats = booking.booked_seats.select_related('seat').all()
    seat_ids = [bs.seat_id for bs in booked_seats]
    if seat_ids and schedule:
        ShowSeat.objects.filter(
            show_schedule=schedule,
            seat_id__in=seat_ids
        ).update(status='available', reserved_by=None, reserved_until=None)

    # Release any ShowSeats reserved by user for this schedule
    if schedule:
        ShowSeat.objects.filter(
            show_schedule=schedule,
            status='reserved',
            reserved_by=booking.user
        ).update(status='available', reserved_by=None, reserved_until=None)

    # Step 2: Transition Booking status to 'cancelled'
    booking.status = 'cancelled'
    booking.save(update_fields=['status', 'updated_at'])

    # Step 3: Re-sync schedule available seats count
    if schedule:
        schedule.sync_available_seats()

    # Step 4: Process Refund on Payment record
    payment = getattr(booking, 'payment', None)
    if not payment and schedule:
        payment = Payment.objects.filter(
            user=booking.user,
            show_schedule=schedule,
            status='success'
        ).order_by('-created_at').first()

    if payment:
        payment.status = 'refunded'
        payment.set_gateway_response({
            **payment.get_gateway_response(),
            'refund_status': 'processed',
            'refund_percentage': pct,
            'refund_amount': float(refund_amount),
            'cancellation_policy': 'PVR Cinemas Standard (BookMyShow/Paytm)',
            'policy_notes': policy_desc,
            'refund_reason': reason,
            'refunded_at': timezone.now().isoformat(),
            'cancelled_by_user_id': cancelled_by.id if cancelled_by else None,
        })
        payment.save(update_fields=['status', 'gateway_response', 'updated_at'])

    return {
        'success': True,
        'booking_reference': booking.booking_reference,
        'refund_amount': float(refund_amount),
        'refund_percentage': pct,
        'formatted_refund_amount': f'₹{refund_amount:,.2f}',
        'policy_description': policy_desc,
        'message': f'Booking {booking.booking_reference} cancelled successfully ({pct}% refund tier). Refund of ₹{refund_amount:,.2f} has been processed.',
    }
