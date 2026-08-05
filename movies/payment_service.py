"""
Payment Service — Razorpay integration.

Responsibilities:
  - Create Razorpay orders (server-side, amount never trusted from client).
  - Verify payment signatures using Razorpay's HMAC-SHA256 mechanism.
  - Confirm bookings atomically only after successful verification.
  - Release seats and mark payment failed on verification failure.
  - Idempotent — safe to call verify multiple times for the same payment.
"""
import hmac
import hashlib
import razorpay
from decimal import Decimal
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from .models import Payment, ShowSchedule


# ---------------------------------------------------------------------------
# Razorpay client
# ---------------------------------------------------------------------------

def _get_client():
    if not settings.RAZORPAY_KEY_ID or not settings.RAZORPAY_KEY_SECRET:
        raise ValueError(
            'Razorpay API keys are not configured. '
            'Set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET as environment variables.'
        )
    return razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))


# ---------------------------------------------------------------------------
# Create order
# ---------------------------------------------------------------------------

def create_payment_order(user, schedule_id):
    """
    Create a Razorpay order for the user's current reserved seats.

    - Amount always calculated server-side.
    - Stores a pending Payment record before returning.
    - Returns dict with all fields the frontend checkout needs.
    """
    from .reservation_service import get_reservation_status

    reservation = get_reservation_status(user, schedule_id)
    if reservation['expired'] or not reservation['seats']:
        raise ValueError(
            'Your seat reservation has expired or no seats are reserved. '
            'Please select seats again.'
        )

    schedule = ShowSchedule.objects.select_related('movie', 'theater').get(pk=schedule_id)

    # Server-side amount calculation
    total_amount = sum(Decimal(str(s['price'])) for s in reservation['seats'])
    if total_amount <= 0:
        raise ValueError('Invalid order amount.')

    currency = getattr(settings, 'RAZORPAY_CURRENCY', 'INR')
    amount_paise = int(total_amount * 100)

    client = _get_client()
    try:
        order_data = client.order.create({
            'amount': amount_paise,
            'currency': currency,
            'receipt': f'bms_u{user.id}_s{schedule_id}',
            'notes': {
                'user_id': str(user.id),
                'schedule_id': str(schedule_id),
                'movie': schedule.movie.title,
                'seats': ', '.join(s['seat_number'] for s in reservation['seats']),
            }
        })
    except razorpay.errors.BadRequestError as e:
        raise ValueError(f'Payment gateway error: {str(e)}')
    except Exception as e:
        raise ValueError(f'Payment gateway error: {str(e)}')

    gateway_order_id = order_data['id']

    payment = Payment.objects.create(
        user=user,
        show_schedule=schedule,
        gateway='razorpay',
        gateway_order_id=gateway_order_id,
        amount=total_amount,
        amount_paise=amount_paise,
        currency=currency,
        status='pending',
    )
    payment.set_gateway_response(order_data)
    payment.save(update_fields=['gateway_response'])

    return {
        'payment_id': payment.id,
        'schedule_id': schedule.id,
        'theater_id': schedule.theater_id,
        'gateway_order_id': gateway_order_id,
        'amount': amount_paise,
        'currency': currency,
        'key_id': settings.RAZORPAY_KEY_ID,
        'movie': schedule.movie.title,
        'theater': schedule.theater.name,
        'show_time': schedule.show_time.isoformat(),
        'seats': [s['seat_number'] for s in reservation['seats']],
        'seconds_remaining': reservation['seconds_remaining'],
        'prefill': {
            'name': user.get_full_name() or user.username,
            'email': user.email,
        },
    }


# ---------------------------------------------------------------------------
# Verify payment — the critical path
# ---------------------------------------------------------------------------

def verify_and_confirm_payment(user, gateway_order_id, gateway_payment_id, gateway_signature):
    """
    Verify Razorpay signature and confirm booking atomically.

    Idempotency:
      - If payment is already 'success' with a booking, return the existing booking.
      - Never creates duplicate bookings.

    On success:
      - Payment status → 'success'
      - Booking is created and confirmed
      - ShowSeats marked 'booked'

    On failure:
      - Payment status → 'failed'
      - Reserved seats released back to 'available'
      - Raises ValueError with reason

    Returns the Booking instance.
    """
    # Fetch payment record
    try:
        payment = Payment.objects.select_related('user').get(gateway_order_id=gateway_order_id)
    except Payment.DoesNotExist:
        raise ValueError('Payment order not found.')

    # Security: verify this payment belongs to the requesting user
    if payment.user_id != user.id:
        raise ValueError('Payment does not belong to this user.')

    # Idempotency: already successfully verified
    if payment.status == 'success' and payment.booking_id:
        return payment.booking

    # Idempotency: already failed — do not retry
    if payment.status in ('failed', 'cancelled'):
        raise ValueError(f'Payment is already marked as {payment.status}.')

    # --- Signature verification ---
    try:
        _verify_razorpay_signature(gateway_order_id, gateway_payment_id, gateway_signature)
    except SignatureVerificationError as e:
        # Verification failed — release seats and fail the payment
        _fail_payment(payment, gateway_payment_id, str(e))
        raise ValueError(str(e))

    # --- Signature valid — confirm booking atomically ---
    from .reservation_service import confirm_booking as _confirm_booking

    try:
        with transaction.atomic():
            booking = _confirm_booking(user, payment.show_schedule_id)
            payment.gateway_payment_id = gateway_payment_id
            payment.gateway_signature = gateway_signature
            payment.status = 'success'
            payment.booking = booking
            payment.set_gateway_response({
                'razorpay_order_id': gateway_order_id,
                'razorpay_payment_id': gateway_payment_id,
                'razorpay_signature': gateway_signature,
                'verified_at': timezone.now().isoformat(),
            })
            payment.save(update_fields=[
                'gateway_payment_id', 'gateway_signature',
                'status', 'booking', 'gateway_response', 'updated_at'
            ])
            return booking
    except ValueError as e:
        # Reservation may have expired between payment and verification
        _fail_payment(payment, gateway_payment_id, str(e))
        raise ValueError(f'Booking failed after payment: {str(e)}')


def record_payment_failure(gateway_order_id, gateway_payment_id, reason='Payment failed or cancelled'):
    """
    Record a failed/cancelled payment attempt.
    Called when the user dismisses the payment modal or payment fails on client.
    Safe to call multiple times — idempotent.
    """
    try:
        payment = Payment.objects.get(gateway_order_id=gateway_order_id)
    except Payment.DoesNotExist:
        return

    # Only update if still pending
    if payment.status != 'pending':
        return

    _fail_payment(payment, gateway_payment_id, reason)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

class SignatureVerificationError(Exception):
    pass


def _verify_razorpay_signature(order_id, payment_id, signature):
    """
    Verify Razorpay payment signature using HMAC-SHA256.
    Raises SignatureVerificationError if invalid.
    """
    if not signature:
        raise SignatureVerificationError('Missing payment signature.')

    expected = hmac.new(
        settings.RAZORPAY_KEY_SECRET.encode('utf-8'),
        f'{order_id}|{payment_id}'.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, signature):
        raise SignatureVerificationError(
            'Payment signature verification failed. This may indicate a tampered request.'
        )


def _fail_payment(payment, gateway_payment_id, reason):
    """Mark a payment as failed and release reserved seats."""
    payment.status = 'failed'
    if gateway_payment_id:
        payment.gateway_payment_id = gateway_payment_id
    payment.set_gateway_response({'failure_reason': reason, 'failed_at': timezone.now().isoformat()})
    payment.save(update_fields=['status', 'gateway_payment_id', 'gateway_response', 'updated_at'])

    # Release reserved seats so other users can book them
    if payment.show_schedule_id and payment.user_id:
        from .models import ShowSeat
        ShowSeat.objects.filter(
            show_schedule_id=payment.show_schedule_id,
            status='reserved',
            reserved_by_id=payment.user_id,
        ).update(status='available', reserved_by=None, reserved_until=None)


# ---------------------------------------------------------------------------
# Webhook handler
# ---------------------------------------------------------------------------

def _verify_webhook_signature(payload_body: bytes, received_signature: str) -> bool:
    """
    Verify Razorpay webhook signature using HMAC-SHA256.
    Razorpay signs with the webhook secret, not the API secret.
    """
    webhook_secret = getattr(settings, 'RAZORPAY_WEBHOOK_SECRET', '')
    if not webhook_secret:
        return False
    expected = hmac.new(
        webhook_secret.encode('utf-8'),
        payload_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, received_signature)


@transaction.atomic
def process_webhook_event(payload_body: bytes, signature: str) -> dict:
    """
    Process a verified Razorpay webhook event.

    Idempotency:
      - Looks up the Payment by gateway_order_id.
      - Only processes events that move the payment forward (pending → success/failed).
      - Duplicate deliveries of the same event are silently skipped.

    Supported events:
      - payment.captured  → mark success, confirm booking
      - payment.failed    → mark failed, release seats
      - order.paid        → same as payment.captured (fallback)
      - refund.created    → mark refunded

    Returns dict with outcome for logging.
    """
    import json as _json

    # Step 1: verify webhook signature
    if not _verify_webhook_signature(payload_body, signature):
        raise SignatureVerificationError('Webhook signature verification failed.')

    try:
        payload = _json.loads(payload_body.decode('utf-8'))
    except ValueError:
        raise ValueError('Invalid webhook payload JSON.')

    event = payload.get('event', '')
    entity = payload.get('payload', {})

    # Extract payment and order data depending on event structure
    payment_entity = entity.get('payment', {}).get('entity', {})
    order_id = payment_entity.get('order_id') or entity.get('order', {}).get('entity', {}).get('id', '')
    razorpay_payment_id = payment_entity.get('id', '')
    error_desc = payment_entity.get('error_description', 'Payment failed')

    if not order_id:
        return {'status': 'skipped', 'reason': 'No order_id in payload'}

    # Step 2: fetch payment row (with lock)
    try:
        payment = Payment.objects.select_for_update().select_related('user').get(gateway_order_id=order_id)
    except Payment.DoesNotExist:
        # Not our order — ignore
        return {'status': 'skipped', 'reason': f'Order {order_id} not found'}

    # Step 3: store raw event for audit
    payment.set_gateway_response({**payment.get_gateway_response(), 'webhook_event': event, 'webhook_payload': payload})

    # Step 4: dispatch by event type
    if event in ('payment.captured', 'order.paid'):
        if payment.status == 'success':
            # Idempotent — already processed
            payment.save(update_fields=['gateway_response'])
            return {'status': 'duplicate', 'reason': 'Payment already confirmed'}

        if payment.status in ('failed', 'cancelled'):
            payment.save(update_fields=['gateway_response'])
            return {'status': 'skipped', 'reason': f'Payment already {payment.status}'}

        # Signature already verified via webhook secret — confirm booking
        from .reservation_service import confirm_booking as _confirm_booking
        try:
            booking = _confirm_booking(payment.user, payment.show_schedule_id)
        except ValueError as e:
            _fail_payment(payment, razorpay_payment_id, str(e))
            return {'status': 'error', 'reason': str(e)}

        payment.gateway_payment_id = razorpay_payment_id
        payment.status = 'success'
        payment.booking = booking
        payment.save(update_fields=['gateway_payment_id', 'status', 'booking', 'gateway_response', 'updated_at'])
        return {'status': 'confirmed', 'booking_reference': booking.booking_reference}

    elif event == 'payment.failed':
        if payment.status != 'pending':
            payment.save(update_fields=['gateway_response'])
            return {'status': 'duplicate', 'reason': 'Already processed'}

        _fail_payment(payment, razorpay_payment_id, error_desc)
        return {'status': 'failed'}

    elif event == 'refund.created':
        refund_entity = entity.get('refund', {}).get('entity', {})
        payment.status = 'refunded'
        payment.gateway_payment_id = refund_entity.get('payment_id', payment.gateway_payment_id)
        payment.save(update_fields=['status', 'gateway_payment_id', 'gateway_response', 'updated_at'])
        return {'status': 'refunded'}

    else:
        # Unknown event — store and skip
        payment.save(update_fields=['gateway_response'])
        return {'status': 'skipped', 'reason': f'Unhandled event: {event}'}
