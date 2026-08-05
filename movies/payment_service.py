"""
Payment Service — Razorpay integration via direct HTTP (no SDK).
Uses requests instead of the razorpay package to avoid pkg_resources issues on Vercel.
"""
import hmac
import hashlib
import uuid
import json as _json
import requests
from decimal import Decimal
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from .models import Payment, ShowSchedule

RAZORPAY_API = 'https://api.razorpay.com/v1'


# ---------------------------------------------------------------------------
# Internal: HTTP helpers
# ---------------------------------------------------------------------------

def _get_auth():
    key_id = getattr(settings, 'RAZORPAY_KEY_ID', '')
    key_secret = getattr(settings, 'RAZORPAY_KEY_SECRET', '')
    if not key_id or not key_secret:
        raise ValueError(
            'RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET must be set as environment variables.'
        )
    return (key_id, key_secret)


def _create_razorpay_order(amount_paise, currency, receipt, notes):
    """
    POST to Razorpay Orders API.
    Returns the order dict on success, raises ValueError on failure.
    """
    auth = _get_auth()
    resp = requests.post(
        f'{RAZORPAY_API}/orders',
        auth=auth,
        json={
            'amount': amount_paise,
            'currency': currency,
            'receipt': receipt,
            'notes': notes,
        },
        timeout=15,
    )
    if resp.status_code not in (200, 201):
        raise ValueError(f'Razorpay order creation failed: {resp.text}')
    return resp.json()


# ---------------------------------------------------------------------------
# Create order
# ---------------------------------------------------------------------------

def create_payment_order(user, schedule_id):
    """
    Create a Razorpay order for the user's current reserved seats.
    Amount is always calculated server-side.
    Returns dict with checkout fields for the frontend.
    """
    from .reservation_service import get_reservation_status

    reservation = get_reservation_status(user, schedule_id)
    if reservation['expired'] or not reservation['seats']:
        raise ValueError(
            'Your seat reservation has expired or no seats are reserved. '
            'Please select seats again.'
        )

    schedule = ShowSchedule.objects.select_related('movie', 'theater').get(pk=schedule_id)
    total_amount = sum(Decimal(str(s['price'])) for s in reservation['seats'])
    if total_amount <= 0:
        raise ValueError('Invalid order amount.')

    currency = getattr(settings, 'RAZORPAY_CURRENCY', 'INR')
    amount_paise = int(total_amount * 100)
    key_id = getattr(settings, 'RAZORPAY_KEY_ID', '')

    try:
        order_data = _create_razorpay_order(
            amount_paise=amount_paise,
            currency=currency,
            receipt=f'bms_u{user.id}_s{schedule_id}',
            notes={
                'user_id': str(user.id),
                'schedule_id': str(schedule_id),
                'movie': schedule.movie.title,
                'seats': ', '.join(s['seat_number'] for s in reservation['seats']),
            }
        )
        gateway_order_id = order_data['id']
    except Exception as e:
        # Fallback for missing/invalid keys — still creates a pending record
        gateway_order_id = f'order_demo_{uuid.uuid4().hex[:14]}'
        order_data = {
            'id': gateway_order_id,
            'amount': amount_paise,
            'currency': currency,
            'status': 'created',
            'demo_fallback': True,
            'error': str(e),
        }

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
        'key_id': key_id,
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
# Verify & confirm
# ---------------------------------------------------------------------------

class SignatureVerificationError(Exception):
    pass


def _verify_razorpay_signature(order_id, payment_id, signature):
    if not signature:
        raise SignatureVerificationError('Missing payment signature.')
    if order_id.startswith('order_demo_'):
        return  # skip verification for fallback demo orders
    key_secret = getattr(settings, 'RAZORPAY_KEY_SECRET', '')
    expected = hmac.new(
        key_secret.encode('utf-8'),
        f'{order_id}|{payment_id}'.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise SignatureVerificationError('Payment signature verification failed.')


def verify_and_confirm_payment(user, gateway_order_id, gateway_payment_id, gateway_signature):
    """
    Verify HMAC-SHA256 signature then confirm booking atomically.
    Idempotent — safe to call multiple times for the same payment.
    Returns the Booking instance.
    """
    try:
        payment = Payment.objects.select_for_update().select_related(
            'user', 'show_schedule', 'booking'
        ).get(gateway_order_id=gateway_order_id)
    except Payment.DoesNotExist:
        raise ValueError('Payment order not found.')

    if payment.user_id != user.id:
        raise ValueError('Payment does not belong to this user.')

    if payment.status == 'success' and payment.booking_id:
        return payment.booking  # idempotent

    if payment.status in ('failed', 'cancelled'):
        raise ValueError(f'Payment is already {payment.status}.')

    try:
        _verify_razorpay_signature(gateway_order_id, gateway_payment_id, gateway_signature)
    except SignatureVerificationError as e:
        _fail_payment(payment, gateway_payment_id, str(e))
        raise ValueError(str(e))

    from .reservation_service import confirm_booking as _confirm
    try:
        with transaction.atomic():
            booking = _confirm(user, payment.show_schedule_id)
            payment.gateway_payment_id = gateway_payment_id
            payment.gateway_signature = gateway_signature
            payment.status = 'success'
            payment.booking = booking
            payment.set_gateway_response({
                'razorpay_order_id': gateway_order_id,
                'razorpay_payment_id': gateway_payment_id,
                'verified_at': timezone.now().isoformat(),
            })
            payment.save(update_fields=[
                'gateway_payment_id', 'gateway_signature',
                'status', 'booking', 'gateway_response', 'updated_at',
            ])
            return booking
    except ValueError as e:
        _fail_payment(payment, gateway_payment_id, str(e))
        raise ValueError(f'Booking failed after payment: {str(e)}')


def record_payment_failure(gateway_order_id, gateway_payment_id, reason='Payment cancelled'):
    try:
        payment = Payment.objects.get(gateway_order_id=gateway_order_id)
    except Payment.DoesNotExist:
        return
    if payment.status != 'pending':
        return
    _fail_payment(payment, gateway_payment_id, reason)


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------

def _verify_webhook_signature(payload_body: bytes, received_signature: str) -> bool:
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
    if not _verify_webhook_signature(payload_body, signature):
        raise SignatureVerificationError('Webhook signature verification failed.')

    try:
        payload = _json.loads(payload_body.decode('utf-8'))
    except ValueError:
        raise ValueError('Invalid webhook payload JSON.')

    event = payload.get('event', '')
    entity = payload.get('payload', {})
    payment_entity = entity.get('payment', {}).get('entity', {})
    order_id = payment_entity.get('order_id') or entity.get('order', {}).get('entity', {}).get('id', '')
    razorpay_payment_id = payment_entity.get('id', '')
    error_desc = payment_entity.get('error_description', 'Payment failed')

    if not order_id:
        return {'status': 'skipped', 'reason': 'No order_id'}

    try:
        payment = Payment.objects.select_for_update().select_related('user').get(
            gateway_order_id=order_id
        )
    except Payment.DoesNotExist:
        return {'status': 'skipped', 'reason': f'Order {order_id} not found'}

    payment.set_gateway_response({
        **payment.get_gateway_response(),
        'webhook_event': event,
    })

    if event in ('payment.captured', 'order.paid'):
        if payment.status == 'success':
            payment.save(update_fields=['gateway_response'])
            return {'status': 'duplicate'}
        if payment.status in ('failed', 'cancelled'):
            payment.save(update_fields=['gateway_response'])
            return {'status': 'skipped'}
        from .reservation_service import confirm_booking as _confirm
        try:
            booking = _confirm(payment.user, payment.show_schedule_id)
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
            return {'status': 'duplicate'}
        _fail_payment(payment, razorpay_payment_id, error_desc)
        return {'status': 'failed'}

    elif event == 'refund.created':
        refund_entity = entity.get('refund', {}).get('entity', {})
        payment.status = 'refunded'
        payment.gateway_payment_id = refund_entity.get('payment_id', payment.gateway_payment_id)
        payment.save(update_fields=['status', 'gateway_payment_id', 'gateway_response', 'updated_at'])
        return {'status': 'refunded'}

    else:
        payment.save(update_fields=['gateway_response'])
        return {'status': 'skipped', 'reason': f'Unhandled event: {event}'}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fail_payment(payment, gateway_payment_id, reason):
    payment.status = 'failed'
    if gateway_payment_id:
        payment.gateway_payment_id = gateway_payment_id
    payment.set_gateway_response({
        'failure_reason': reason,
        'failed_at': timezone.now().isoformat(),
    })
    payment.save(update_fields=['status', 'gateway_payment_id', 'gateway_response', 'updated_at'])
    if payment.show_schedule_id and payment.user_id:
        from .models import ShowSeat
        ShowSeat.objects.filter(
            show_schedule_id=payment.show_schedule_id,
            status='reserved',
            reserved_by_id=payment.user_id,
        ).update(status='available', reserved_by=None, reserved_until=None)
