import logging
from datetime import timedelta
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import JsonResponse

logger = logging.getLogger(__name__)


from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q, Count
from django.utils import timezone
from .models import (
    Movie, MovieImage, Genre, Language, CastMember, Theater, Screen, Seat, ShowSeat, Booking,
    ShowSchedule, BookingSeat, Review, ReportedReview, Payment
)
from .forms import (
    ReviewForm, ReportReviewForm, MovieForm, GenreForm, LanguageForm,
    CastMemberForm, TheaterForm, ScreenForm, ShowScheduleForm
)




def cleanup_completed_schedules():
    """
    Releases expired temporary seat reservations.
    Past show schedules are kept for booking and payment audit history.
    """
    now = timezone.now()
    expired_reservations = ShowSeat.objects.filter(status='reserved', reserved_until__lt=now)
    count = expired_reservations.count()
    if count > 0:
        expired_reservations.update(status='available', reserved_by=None, reserved_until=None)
    return count


from django.contrib.auth import get_user_model
from functools import wraps
from django.http import HttpResponseForbidden


def is_staff_user(user):
    return bool(user and user.is_authenticated and (user.is_staff or user.is_superuser))


def staff_or_admin_required(view_func):
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")
        if not (request.user.is_staff or request.user.is_superuser):
            return HttpResponseForbidden(
                '<div style="font-family: sans-serif; text-align: center; margin-top: 100px;">'
                '  <h1 style="color: #dc3545;">403 Forbidden</h1>'
                '  <p>Administrator or Staff permissions are required to access this section.</p>'
                '  <a href="/" style="color: #007bff;">Return to Home Page</a>'
                '</div>'
            )
        return view_func(request, *args, **kwargs)
    return _wrapped_view


from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q, Count, Min, F
from django.utils import timezone


from .discovery_service import MovieDiscoveryService


def build_filtered_movies_queryset(params):
    return MovieDiscoveryService.build_filtered_queryset(params)


def apply_sorting(queryset, sort_by):
    return MovieDiscoveryService.apply_sorting(queryset, sort_by)


def movie_list(request):
    cleanup_completed_schedules()
    context = MovieDiscoveryService.get_discovery_context(request, per_page=9)
    return render(request, 'movies/movie_list.html', context)



def theater_list(request, movie_id):
    """
    Displays theaters and UPCOMING show schedules for a movie.
    Past/expired schedules are automatically filtered out and deleted.
    """
    cleanup_completed_schedules()
    now = timezone.now()
    movie = get_object_or_404(Movie, id=movie_id)

    # Only show upcoming schedules
    schedules = (
        ShowSchedule.objects
        .filter(movie=movie, show_time__gte=now)
        .select_related('theater', 'screen')
        .order_by('show_time')
    )

    # Only show theaters that have upcoming schedules for this movie
    theaters = (
        Theater.objects
        .filter(schedules__movie=movie, schedules__show_time__gte=now)
        .distinct()
        .prefetch_related('screens')
    )

    # Group upcoming schedules by theater
    theater_schedules = {}
    for schedule in schedules:
        theater_schedules.setdefault(schedule.theater_id, []).append(schedule)

    return render(request, 'movies/theater_list.html', {
        'movie': movie,
        'theaters': theaters,
        'schedules': schedules,
        'theater_schedules': theater_schedules,
    })


@login_required(login_url='/login/')
def book_seats(request, theater_id):
    """
    Handles GET & POST for /movies/theater/<theater_id>/seats/book/

    GET: Renders seat map (seat_selection.html) for specified schedule_id or next upcoming schedule.
    POST: Reserves seats, creates payment order, and renders Razorpay checkout page (checkout.html).
    """
    theater = get_object_or_404(Theater, id=theater_id)
    now = timezone.now()

    schedule_id = request.POST.get('schedule_id') or request.GET.get('schedule_id')

    schedule = None
    if schedule_id and str(schedule_id).isdigit():
        schedule = ShowSchedule.objects.filter(id=int(schedule_id)).select_related('movie', 'theater', 'screen').first()

    if not schedule:
        schedule = ShowSchedule.objects.filter(theater=theater, show_time__gte=now).select_related('movie', 'theater', 'screen').order_by('show_time').first()

    if not schedule:
        schedule = ShowSchedule.objects.filter(theater=theater).select_related('movie', 'theater', 'screen').order_by('-show_time').first()

    if not schedule:
        schedule = ShowSchedule.objects.filter(show_time__gte=now).select_related('movie', 'theater', 'screen').order_by('show_time').first()

    if not schedule:
        schedule = ShowSchedule.objects.order_by('-show_time').select_related('movie', 'theater', 'screen').first()

    if not schedule:
        messages.error(request, 'No show schedules available in the system. Please create a schedule in Admin.')
        return redirect('movie_list')

    # If POST request with selected seats -> Reserve & Launch Razorpay Checkout
    if request.method == 'POST':
        raw_seats = request.POST.getlist('seats') or request.POST.get('seats', '').split(',')
        selected_seat_ids = []
        for item in raw_seats:
            if isinstance(item, str):
                for part in item.split(','):
                    part = part.strip()
                    if part.isdigit():
                        selected_seat_ids.append(int(part))
            elif isinstance(item, int):
                selected_seat_ids.append(item)

        if not selected_seat_ids:
            messages.error(request, 'Please select at least one seat to proceed.')
            return redirect(f"{reverse('book_seats', args=[theater_id])}?schedule_id={schedule.id}")

        from .reservation_service import reserve_seats
        from .payment_service import create_payment_order

        # Reserve seats for the requesting user
        try:
            reserve_seats(request.user, schedule.id, selected_seat_ids)
        except Exception as e:
            messages.error(request, f'Seat reservation notice: {str(e)}')
            return redirect(f"{reverse('book_seats', args=[theater_id])}?schedule_id={schedule.id}")

        # Create Razorpay payment order
        try:
            order_data = create_payment_order(request.user, schedule.id)
        except Exception as e:
            messages.error(request, f'Could not initiate payment: {str(e)}')
            return redirect(f"{reverse('book_seats', args=[theater_id])}?schedule_id={schedule.id}")

        import json
        amount_rupees = float(order_data.get('amount', 0)) / 100.0 if order_data.get('amount') else 0.0

        try:
            return render(request, 'movies/checkout.html', {
                'order_data': order_data,
                'order_data_json': json.dumps(order_data),
                'schedule': schedule,
                'razorpay_key_id': order_data.get('key_id', ''),
                'amount_in_rupees': amount_rupees,
            })
        except Exception as render_err:
            messages.error(request, f'Checkout rendering note: {str(render_err)}')
            return redirect(f"{reverse('book_seats', args=[theater_id])}?schedule_id={schedule.id}")

    # GET Request: Render Seat Selection Map
    screen = schedule.screen if schedule.screen else theater.screens.first()
    if screen:
        screen.generate_seats()
        all_seats = Seat.objects.filter(screen=screen, is_active=True).order_by('row', 'number')
        if not schedule.show_seats.exists():
            schedule._generate_show_seats()
    else:
        all_seats = Seat.objects.filter(theater=theater, is_active=True).order_by('seat_number')

    # Clean up stale reservations & sync live available count
    from .reservation_service import _release_stale
    _release_stale(schedule.id)
    schedule.sync_available_seats()

    confirmed_booked_ids = set(
        BookingSeat.objects.filter(show_schedule=schedule)
        .values_list('seat_id', flat=True)
    )
    reserved_by_others_ids = set(
        ShowSeat.objects.filter(
            show_schedule=schedule,
            status__in=['booked', 'reserved'],
            reserved_until__gt=now
        )
        .exclude(reserved_by=request.user)
        .values_list('seat_id', flat=True)
    )
    booked_seat_ids = confirmed_booked_ids | reserved_by_others_ids

    base_price = schedule.price
    tier_groups = {'regular': {}, 'premium': {}, 'recliner': {}}
    tier_prices = {}

    for s in all_seats:
        s.is_already_booked = s.id in booked_seat_ids
        s.calculated_price = round(float(base_price) * float(s.price_multiplier), 2)
        tier_prices[s.seat_type] = s.calculated_price

        tier = s.seat_type if s.seat_type in tier_groups else 'regular'
        tier_groups[tier].setdefault(s.row, []).append(s)

    effective_available_seats = max(0, schedule.available_seats - len(reserved_by_others_ids))

    return render(request, 'movies/seat_selection.html', {
        'theaters': theater,
        'schedule': schedule,
        'screen': screen,
        'tier_groups': tier_groups,
        'tier_prices': tier_prices,
        'seats': all_seats,
        'effective_available_seats': effective_available_seats,
    })



@login_required(login_url='/login/')
def add_review(request, movie_id):
    movie = get_object_or_404(Movie, id=movie_id)

    # Prevent duplicate
    existing = Review.objects.filter(user=request.user, movie=movie).first()
    if existing:
        messages.info(request, 'You have already reviewed this movie. Edit your review instead.')
        return redirect('edit_review', review_id=existing.id)

    # Check 1: confirmed booking exists for this movie
    user_bookings = Booking.objects.filter(
        user=request.user,
        status__in=['confirmed', 'completed'],
    ).filter(
        Q(show_schedule__movie=movie) | Q(movie=movie)
    )

    if not user_bookings.exists():
        messages.error(request, 'You can only review a movie after booking a ticket.')
        return redirect('movie_detail', movie_id=movie.id)

    # Check 2: show schedule must have passed
    past_booking_exists = user_bookings.filter(
        show_schedule__show_time__lt=timezone.now()
    ).exists()

    if not past_booking_exists:
        messages.warning(request, 'Review form unlocks only after your booked show schedule has passed.')
        return redirect('movie_detail', movie_id=movie.id)

    if request.method == 'POST':
        form = ReviewForm(request.POST)
        if form.is_valid():
            review = form.save(commit=False)
            review.user = request.user
            review.movie = movie
            review.save()
            messages.success(request, 'Review submitted successfully.')
            return redirect('movie_detail', movie_id=movie.id)
    else:
        form = ReviewForm()

    return render(request, 'movies/review_form.html', {
        'form': form,
        'movie': movie,
        'action': 'Add',
    })


@login_required(login_url='/login/')
def edit_review(request, review_id):
    review = get_object_or_404(Review, id=review_id, user=request.user)
    movie = review.movie

    if request.method == 'POST':
        form = ReviewForm(request.POST, instance=review)
        if form.is_valid():
            try:
                form.save()
                messages.success(request, 'Review updated.')
                return redirect('movie_detail', movie_id=movie.id)
            except ValidationError as e:
                form.add_error(None, e)
    else:
        form = ReviewForm(instance=review)

    return render(request, 'movies/review_form.html', {
        'form': form,
        'movie': movie,
        'action': 'Edit',
    })


@login_required(login_url='/login/')
def delete_review(request, review_id):
    review = get_object_or_404(Review, id=review_id, user=request.user)
    movie_id = review.movie.id
    if request.method == 'POST':
        review.delete()
        messages.success(request, 'Review deleted.')
    return redirect('movie_detail', movie_id=movie_id)


def movie_detail(request, movie_id):
    from django.utils import timezone
    from .recommendations import get_similar_movies, get_trending_movies, get_recently_released

    movie = get_object_or_404(
        Movie.objects.prefetch_related('genres', 'languages', 'cast', 'images', 'reviews__user'),
        id=movie_id
    )

    reviews = movie.reviews.filter(is_active=True).select_related('user')
    user_review = reviews.filter(user=request.user).first() if request.user.is_authenticated else None
    other_reviews = reviews.exclude(user=request.user) if request.user.is_authenticated else reviews

    # Check if user can review (must have confirmed booking whose showtime has passed)
    can_review = False
    review_lock_reason = None
    if request.user.is_authenticated:
        user_bookings = Booking.objects.filter(
            user=request.user,
            status__in=['confirmed', 'completed']
        ).filter(
            Q(show_schedule__movie=movie) | Q(movie=movie)
        )
        if not user_bookings.exists():
            review_lock_reason = "You must book a ticket to review this movie."
        else:
            past_booking = user_bookings.filter(
                show_schedule__show_time__lt=timezone.now()
            ).first()
            if past_booking:
                can_review = True
            else:
                upcoming_booking = user_bookings.order_by('show_schedule__show_time').first()
                if upcoming_booking and upcoming_booking.show_schedule:
                    show_str = upcoming_booking.show_schedule.show_time.strftime('%b %d, %Y at %I:%M %p')
                    review_lock_reason = f"Review unlocks after your show on {show_str}."
                else:
                    review_lock_reason = "Review unlocks after your showtime has passed."

    # Meta info string built in Python — avoids inline template conditionals
    meta_parts = []
    if movie.release_date:
        meta_parts.append(movie.release_date.strftime('%b %d, %Y'))
    if movie.duration_minutes:
        meta_parts.append(f'{movie.duration_minutes} min')
    if movie.age_certification:
        meta_parts.append(movie.age_certification)
    movie_meta = ' · '.join(meta_parts)

    # Upcoming schedules grouped by theater
    upcoming_schedules = (
        ShowSchedule.objects
        .filter(movie=movie, show_time__gte=timezone.now())
        .select_related('theater')
        .order_by('theater__name', 'show_time')
    )
    schedules_by_theater = {}
    for schedule in upcoming_schedules:
        schedules_by_theater.setdefault(schedule.theater.name, []).append(schedule)

    # Recommendations
    similar = list(get_similar_movies(movie, limit=6))
    similar_ids = {m.pk for m in similar} | {movie.pk}
    trending = list(get_trending_movies(exclude_ids=similar_ids, limit=10))
    trending_ids = similar_ids | {m.pk for m in trending}
    recently_released = list(get_recently_released(exclude_ids=trending_ids, limit=10))

    return render(request, 'movies/movie_detail.html', {
        'movie': movie,
        'movie_meta': movie_meta,
        'reviews': other_reviews,
        'user_review': user_review,
        'can_review': can_review,
        'review_lock_reason': review_lock_reason,
        'schedules_by_theater': schedules_by_theater,
        'gallery': movie.images.all(),
        'cast': movie.cast.all(),
        'similar_movies': similar,
        'trending_movies': trending,
        'recently_released': recently_released,
    })



@login_required(login_url='/login/')
def report_review(request, review_id):
    review = get_object_or_404(Review, id=review_id, is_active=True)

    # Users cannot report their own reviews
    if review.user == request.user:
        messages.error(request, 'You cannot report your own review.')
        return redirect('movie_detail', movie_id=review.movie.id)

    # Prevent duplicate reports
    already_reported = ReportedReview.objects.filter(
        review=review, reported_by=request.user
    ).exists()
    if already_reported:
        messages.info(request, 'You have already reported this review.')
        return redirect('movie_detail', movie_id=review.movie.id)

    if request.method == 'POST':
        form = ReportReviewForm(request.POST)
        if form.is_valid():
            report = form.save(commit=False)
            report.review = review
            report.reported_by = request.user
            report.save()
            messages.success(request, 'Review reported. Our moderation team will look into it.')
            return redirect('movie_detail', movie_id=review.movie.id)
    else:
        form = ReportReviewForm()

    return render(request, 'movies/report_review.html', {
        'form': form,
        'review': review,
    })


def seat_map_api(request, schedule_id):
    """
    JSON API — returns all seats for a show schedule with their current status.
    Expires stale reservations before returning data.
    """
    from .reservation_service import _expire_stale_reservations_by_id
    schedule = get_object_or_404(
        ShowSchedule.objects.select_related('screen', 'theater', 'movie'),
        id=schedule_id
    )

    # Auto-generate ShowSeat records if missing
    if not schedule.show_seats.exists() and schedule.screen:
        schedule._generate_show_seats()

    # Clean up expired reservations and sync unbooked count
    _expire_stale_reservations_by_id(schedule_id)
    total_unbooked = schedule.sync_available_seats()

    show_seats = (
        schedule.show_seats
        .select_related('seat', 'reserved_by')
        .order_by('seat__row', 'seat__number')
    )

    current_user_id = request.user.id if request.user.is_authenticated else None
    now = timezone.now()

    reserved_by_others_count = 0
    rows = {}
    for ss in show_seats:
        seat = ss.seat
        row = seat.row
        is_mine = (ss.reserved_by_id == current_user_id and ss.status == 'reserved' and ss.reserved_until and ss.reserved_until >= now)
        is_reserved_by_other = (ss.status == 'reserved' and ss.reserved_until and ss.reserved_until >= now and not is_mine)

        if is_reserved_by_other:
            reserved_by_others_count += 1

        rem_sec = max(0, int((ss.reserved_until - now).total_seconds())) if (is_mine and ss.reserved_until) else 0
        rows.setdefault(row, []).append({
            'id': ss.id,
            'seat_id': seat.id,
            'seat_number': seat.seat_number,
            'row': seat.row,
            'number': seat.number,
            'seat_type': seat.seat_type,
            'seat_type_label': seat.get_seat_type_display(),
            'price': seat.calculate_price(schedule.price),
            'status': ss.status,
            'is_active': seat.is_active,
            'is_mine': is_mine,
            'is_reserved_by_other': is_reserved_by_other,
            'seconds_remaining': rem_sec,
        })

    available_for_user = max(0, total_unbooked - reserved_by_others_count)

    return JsonResponse({
        'schedule_id': schedule.id,
        'movie': schedule.movie.title,
        'theater': schedule.theater.name,
        'screen': schedule.screen.name if schedule.screen else None,
        'show_time': schedule.show_time.isoformat(),
        'base_price': float(schedule.price),
        'available_seats': available_for_user,
        'total_unbooked': total_unbooked,
        'rows': rows,
        'summary': {
            'total': show_seats.count(),
            'available': show_seats.filter(status='available').count(),
            'booked': show_seats.filter(status='booked').count(),
            'reserved': show_seats.filter(status='reserved', reserved_until__gte=now).count(),
        }
    })


@login_required(login_url='/login/')
def reserve_seats_api(request, schedule_id):
    """
    POST /movies/schedule/<id>/reserve/
    Body: { "seat_ids": [1, 2, 3] }
    Reserves selected seats for 2 minutes. Releases any prior reservations by this user.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    import json
    from .reservation_service import reserve_seats

    try:
        data = json.loads(request.body)
        seat_ids = data.get('seat_ids', [])
        if not isinstance(seat_ids, list):
            raise ValueError('seat_ids must be a list.')
        reserved = reserve_seats(request.user, schedule_id, seat_ids)
    except (ValueError, KeyError) as e:
        return JsonResponse({'error': str(e)}, status=400)
    except ShowSchedule.DoesNotExist:
        return JsonResponse({'error': 'Schedule not found.'}, status=404)

    expiry = reserved[0].reserved_until if reserved else None
    return JsonResponse({
        'reserved': [
            {
                'show_seat_id': ss.pk,
                'seat_number': ss.seat.seat_number,
                'seat_type': ss.seat.get_seat_type_display(),
                'price': ss.seat.calculate_price(ShowSchedule.objects.get(pk=schedule_id).price),
            }
            for ss in reserved
        ],
        'expires_at': expiry.isoformat() if expiry else None,
        'seconds_remaining': reserved[0].seconds_remaining if reserved else 0,
    })


@login_required(login_url='/login/')
def release_seats_api(request, schedule_id):
    """
    POST /movies/schedule/<id>/release/
    Releases all seats currently reserved by this user for the schedule.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    from .reservation_service import release_user_reservations
    count = release_user_reservations(request.user, schedule_id)
    return JsonResponse({'released': count})


@login_required(login_url='/login/')
def reservation_status_api(request, schedule_id):
    """
    GET /movies/schedule/<id>/reservation-status/
    Returns remaining time and seat list for this user's current reservation.
    """
    from .reservation_service import get_reservation_status
    status = get_reservation_status(request.user, schedule_id)
    return JsonResponse(status)




@login_required(login_url='/login/')
def confirm_booking_api(request, schedule_id):
    """
    POST /movies/schedule/<id>/confirm-booking/

    Atomically converts all reserved seats into a confirmed Booking.
    Uses select_for_update() — safe under concurrent requests.

    Success response:
    {
        "booking_reference": "BMS...",
        "seats": [...],
        "total_price": 450.00,
        "available_seats": 74
    }

    Error response (400):
    { "error": "Reservation expired for seats: A1, A2." }
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required.'}, status=405)

    from .reservation_service import confirm_booking

    try:
        booking = confirm_booking(request.user, schedule_id)
    except ValueError as e:
        return JsonResponse({'error': str(e)}, status=400)
    except ShowSchedule.DoesNotExist:
        return JsonResponse({'error': 'Schedule not found.'}, status=404)
    except Exception as e:
        return JsonResponse({'error': f'Booking failed: {str(e)}'}, status=500)

    booked_seats = booking.booked_seats.select_related('seat').all()

    return JsonResponse({
        'success': True,
        'booking_reference': booking.booking_reference,
        'movie': booking.movie.title if booking.movie else '',
        'theater': booking.theater.name if booking.theater else '',
        'show_time': booking.show_schedule.show_time.isoformat(),
        'seats': [
            {
                'seat_number': bs.seat.seat_number,
                'seat_type': bs.seat.get_seat_type_display(),
                'price': float(bs.price),
            }
            for bs in booked_seats
        ],
        'number_of_seats': booking.number_of_seats,
        'total_price': float(booking.total_price),
        'available_seats': booking.show_schedule.available_seats,
    })


@login_required(login_url='/login/')
def create_payment_order_api(request, schedule_id):
    """
    POST /movies/schedule/<id>/create-payment-order/

    Creates a Razorpay order server-side for the user's reserved seats.
    Amount is always calculated on the server — never trusted from client.

    Success response:
    {
        "payment_id": 1,
        "gateway_order_id": "order_xxx",
        "amount": 45000,
        "currency": "INR",
        "key_id": "rzp_test_xxx",
        "movie": "...",
        "seats": ["A1", "A2"],
        "seconds_remaining": 87,
        "prefill": { "name": "...", "email": "..." }
    }

    Error response (400):
    { "error": "Your seat reservation has expired." }
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required.'}, status=405)

    from .payment_service import create_payment_order

    try:
        order_data = create_payment_order(request.user, schedule_id)
    except ValueError as e:
        return JsonResponse({'error': str(e)}, status=400)
    except ShowSchedule.DoesNotExist:
        return JsonResponse({'error': 'Schedule not found.'}, status=404)
    except Exception as e:
        return JsonResponse({'error': f'Payment order creation failed: {str(e)}'}, status=500)

    return JsonResponse(order_data)


@login_required(login_url='/login/')
def get_payment_status_api(request, payment_id):
    """
    GET /movies/payment/<id>/status/
    Returns the current status of a payment order for the requesting user.
    """
    from .models import Payment
    try:
        payment = Payment.objects.select_related('booking', 'show_schedule__movie').get(
            id=payment_id, user=request.user
        )
    except Payment.DoesNotExist:
        return JsonResponse({'error': 'Payment not found.'}, status=404)

    return JsonResponse({
        'payment_id': payment.id,
        'gateway_order_id': payment.gateway_order_id,
        'gateway_payment_id': payment.gateway_payment_id or None,
        'gateway': payment.gateway,
        'status': payment.status,
        'amount': float(payment.amount),
        'currency': payment.currency,
        'booking_reference': payment.booking.booking_reference if payment.booking else None,
        'created_at': payment.created_at.isoformat(),
    })


@login_required(login_url='/login/')
def verify_payment_api(request):
    """
    POST /movies/payment/verify/

    Called by the frontend after Razorpay checkout succeeds.
    Verifies the HMAC-SHA256 signature server-side, then confirms the booking.

    Request body:
    {
        "razorpay_order_id":   "order_xxx",
        "razorpay_payment_id": "pay_xxx",
        "razorpay_signature":  "abc123..."
    }

    Success response:
    {
        "success": true,
        "booking_reference": "BMS...",
        "seats": [...],
        "total_price": 450.0
    }

    Failure response (400):
    { "error": "Payment signature verification failed." }
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required.'}, status=405)

    import json as _json
    from .payment_service import verify_and_confirm_payment

    try:
        data = _json.loads(request.body)
    except ValueError:
        return JsonResponse({'error': 'Invalid JSON body.'}, status=400)

    order_id = data.get('razorpay_order_id', '').strip()
    payment_id = data.get('razorpay_payment_id', '').strip()
    signature = data.get('razorpay_signature', '').strip()

    if not order_id or not payment_id or not signature:
        return JsonResponse({'error': 'Missing required fields: razorpay_order_id, razorpay_payment_id, razorpay_signature.'}, status=400)

    try:
        booking = verify_and_confirm_payment(request.user, order_id, payment_id, signature)
    except ValueError as e:
        # Check if booking was already confirmed by webhook — return success in that case
        from .models import Payment
        try:
            payment = Payment.objects.select_related('booking').get(
                gateway_order_id=order_id, user=request.user
            )
            if payment.status == 'success' and payment.booking:
                booking = payment.booking
            else:
                return JsonResponse({'error': str(e)}, status=400)
        except Payment.DoesNotExist:
            return JsonResponse({'error': str(e)}, status=400)
    except Exception as e:
        # Last resort — check if payment succeeded anyway (e.g. via webhook)
        from .models import Payment
        try:
            payment = Payment.objects.select_related('booking').get(
                gateway_order_id=order_id, user=request.user
            )
            if payment.status == 'success' and payment.booking:
                booking = payment.booking
            else:
                return JsonResponse({'error': f'Unexpected error: {str(e)}'}, status=500)
        except Payment.DoesNotExist:
            return JsonResponse({'error': f'Unexpected error: {str(e)}'}, status=500)

    booked_seats = booking.booked_seats.select_related('seat').all()

    return JsonResponse({
        'success': True,
        'booking_reference': booking.booking_reference,
        'movie': booking.movie.title if booking.movie else '',
        'theater': booking.theater.name if booking.theater else '',
        'show_time': booking.show_schedule.show_time.isoformat() if booking.show_schedule else '',
        'seats': [
            {
                'seat_number': bs.seat.seat_number,
                'seat_type': bs.seat.get_seat_type_display(),
                'price': float(bs.price),
            }
            for bs in booked_seats
        ],
        'number_of_seats': booking.number_of_seats,
        'total_price': float(booking.total_price),
    })


@login_required(login_url='/login/')
def record_payment_failure_api(request):
    """
    POST /movies/payment/failed/

    Called by the frontend when the user cancels or payment fails on the Razorpay modal.
    Marks the payment as failed and releases reserved seats.

    Request body:
    {
        "razorpay_order_id": "order_xxx",
        "razorpay_payment_id": "pay_xxx",   (optional)
        "reason": "Payment cancelled by user"  (optional)
    }
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required.'}, status=405)

    import json as _json
    from .payment_service import record_payment_failure

    try:
        data = _json.loads(request.body)
    except ValueError:
        return JsonResponse({'error': 'Invalid JSON body.'}, status=400)

    order_id = data.get('razorpay_order_id', '').strip()
    if not order_id:
        return JsonResponse({'error': 'razorpay_order_id is required.'}, status=400)

    gateway_payment_id = data.get('razorpay_payment_id', '')
    reason = data.get('reason', 'Payment failed or cancelled by user')

    record_payment_failure(order_id, gateway_payment_id, reason)

    return JsonResponse({'success': True, 'message': 'Payment failure recorded. Seats have been released.'})


def razorpay_webhook(request):
    """
    POST /movies/payment/webhook/razorpay/

    Receives Razorpay webhook events.
    - Verifies webhook signature using RAZORPAY_WEBHOOK_SECRET (not the API secret).
    - Processes events idempotently.
    - Always returns 200 so Razorpay does not retry on app errors.
    - Returns 400 only for signature failures (tells Razorpay the payload was bad).

    Configure in Razorpay Dashboard → Webhooks → Active Events:
      payment.captured, payment.failed, order.paid, refund.created
    """
    from .payment_service import process_webhook_event, SignatureVerificationError

    if request.method != 'POST':
        return JsonResponse({'error': 'POST required.'}, status=405)

    signature = request.headers.get('X-Razorpay-Signature', '')
    if not signature:
        return JsonResponse({'error': 'Missing webhook signature.'}, status=400)

    try:
        result = process_webhook_event(request.body, signature)
    except SignatureVerificationError as e:
        # Return 400 — Razorpay will retry, but we want to surface bad signatures
        return JsonResponse({'error': str(e)}, status=400)
    except Exception:
        # Return 200 to stop retries for app-level errors — log internally
        return JsonResponse({'status': 'error'}, status=200)

    return JsonResponse({'status': result.get('status', 'ok')}, status=200)

from .analytics_service import get_full_admin_analytics
from .csv_export_service import export_report_to_csv


@staff_or_admin_required
def admin_dashboard(request):
    start_date = request.GET.get('start_date', '').strip()
    end_date = request.GET.get('end_date', '').strip()

    context = get_full_admin_analytics(start_date, end_date)

    return render(request, 'movies/custom_admin/dashboard.html', context)


@staff_or_admin_required
def admin_export_csv(request):
    """
    GET /movies/manage/export/csv/?report_type=...&start_date=...&end_date=...

    Streams CSV export file for specified report_type filtered by date range.
    """
    report_type = request.GET.get('report_type', 'summary').strip()
    start_date = request.GET.get('start_date', '').strip()
    end_date = request.GET.get('end_date', '').strip()

    return export_report_to_csv(report_type, start_date, end_date)






@user_passes_test(is_staff_user, login_url='/login/')
def admin_manage_movies(request):
    search = request.GET.get('search', '').strip()
    movies = Movie.objects.prefetch_related('genres', 'languages').all()
    if search:
        movies = movies.filter(title__icontains=search)
    return render(request, 'movies/custom_admin/manage_movies.html', {
        'movies': movies,
        'search': search,
    })


@staff_or_admin_required
def admin_movie_form(request, movie_id=None):
    movie = get_object_or_404(Movie, id=movie_id) if movie_id else None

    # Handle quick gallery poster deletion if requested from movie edit form
    if request.method == 'POST' and 'delete_gallery_id' in request.POST and movie:
        del_id = request.POST.get('delete_gallery_id')
        MovieImage.objects.filter(id=del_id, movie=movie).delete()
        messages.success(request, 'Gallery poster deleted successfully.')
        return redirect('admin_movie_edit', movie_id=movie.id)

    if request.method == 'POST':
        form = MovieForm(request.POST, request.FILES, instance=movie)
        if form.is_valid():
            saved_movie = form.save()

            # Process Movie-Specific Genres (Comma-Separated)
            genre_names = form.cleaned_data.get('genre_names', '')
            if genre_names:
                for g_name in [g.strip() for g in genre_names.split(',') if g.strip()]:
                    Genre.objects.get_or_create(movie=saved_movie, name=g_name)

            # Process Movie-Specific Languages (Comma-Separated)
            language_names = form.cleaned_data.get('language_names', '')
            if language_names:
                for l_name in [l.strip() for l in language_names.split(',') if l.strip()]:
                    Language.objects.get_or_create(movie=saved_movie, name=l_name)

            # Process Movie-Specific Cast (Comma-Separated)
            cast_names = form.cleaned_data.get('cast_names', '')
            if cast_names:
                for c_name in [c.strip() for c in cast_names.split(',') if c.strip()]:
                    CastMember.objects.get_or_create(movie=saved_movie, name=c_name, defaults={'role': 'actor'})

            # Process Multi-Poster / Gallery Images Upload
            gallery_files = request.FILES.getlist('gallery_images')
            if gallery_files:
                uploaded_count = 0
                for img_file in gallery_files:
                    MovieImage.objects.create(movie=saved_movie, image=img_file, caption=f"Poster {saved_movie.images.count() + 1}")
                    uploaded_count += 1
                messages.info(request, f'Uploaded {uploaded_count} additional gallery poster(s).')

            messages.success(request, f'Movie "{saved_movie.title}" saved successfully.')
            return redirect('admin_manage_movies')
    else:
        form = MovieForm(instance=movie)

    genres = movie.genres.all() if movie else []
    languages = movie.languages.all() if movie else []
    cast_members = movie.cast.all() if movie else []
    gallery_images = movie.images.all() if movie else []

    return render(request, 'movies/custom_admin/movie_form.html', {
        'form': form,
        'movie': movie,
        'movie_genres': genres,
        'movie_languages': languages,
        'movie_cast': cast_members,
        'gallery_images': gallery_images,
    })



@staff_or_admin_required
def api_quick_add_genre(request):
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        movie_id = request.POST.get('movie_id')
        if not name:
            return JsonResponse({'error': 'Genre name is required.'}, status=400)
        movie = get_object_or_404(Movie, id=movie_id) if movie_id else None
        genre = Genre.objects.create(movie=movie, name=name)
        return JsonResponse({'id': genre.id, 'name': genre.name})
    return JsonResponse({'error': 'Invalid method'}, status=405)


@staff_or_admin_required
def api_quick_delete_genre(request, genre_id):
    if request.method == 'POST':
        genre = get_object_or_404(Genre, id=genre_id)
        name = genre.name
        genre.delete()
        return JsonResponse({'status': 'ok', 'id': genre_id, 'name': name})
    return JsonResponse({'error': 'Invalid method'}, status=405)


@staff_or_admin_required
def api_quick_add_language(request):
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        movie_id = request.POST.get('movie_id')
        if not name:
            return JsonResponse({'error': 'Language name is required.'}, status=400)
        movie = get_object_or_404(Movie, id=movie_id) if movie_id else None
        lang = Language.objects.create(movie=movie, name=name)
        return JsonResponse({'id': lang.id, 'name': lang.name})
    return JsonResponse({'error': 'Invalid method'}, status=405)


@staff_or_admin_required
def api_quick_delete_language(request, language_id):
    if request.method == 'POST':
        lang = get_object_or_404(Language, id=language_id)
        name = lang.name
        lang.delete()
        return JsonResponse({'status': 'ok', 'id': language_id, 'name': name})
    return JsonResponse({'error': 'Invalid method'}, status=405)


@staff_or_admin_required
def api_quick_add_cast(request):
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        role = request.POST.get('role', 'actor').strip()
        movie_id = request.POST.get('movie_id')
        photo = request.FILES.get('photo')
        if not name:
            return JsonResponse({'error': 'Cast member name is required.'}, status=400)
        movie = get_object_or_404(Movie, id=movie_id) if movie_id else None
        cast = CastMember.objects.create(movie=movie, name=name, role=role)
        if photo:
            cast.photo = photo
            cast.save()
        return JsonResponse({'id': cast.id, 'name': cast.name, 'role': cast.get_role_display()})
    return JsonResponse({'error': 'Invalid method'}, status=405)


@staff_or_admin_required
def api_quick_delete_cast(request, cast_id):
    if request.method == 'POST':
        cast = get_object_or_404(CastMember, id=cast_id)
        name = cast.name
        cast.delete()
        return JsonResponse({'status': 'ok', 'id': cast_id, 'name': name})
    return JsonResponse({'error': 'Invalid method'}, status=405)



@staff_or_admin_required
def api_bulk_delete_taxonomies(request):
    """
    POST /movies/manage/api/taxonomies/bulk-delete/
    Deletes a list of selected Genre, Language, or Cast IDs in a single batch query.
    """
    if request.method == 'POST':
        try:
            if request.content_type == 'application/json':
                import json
                data = json.loads(request.body)
                item_type = data.get('item_type')
                ids = data.get('ids', [])
            else:
                item_type = request.POST.get('item_type')
                ids = request.POST.getlist('ids[]') or request.POST.getlist('ids')
        except Exception:
            return JsonResponse({'error': 'Invalid request body'}, status=400)

        if not item_type or not ids:
            return JsonResponse({'error': 'item_type and non-empty ids list are required.'}, status=400)

        try:
            int_ids = [int(i) for i in ids]
        except (ValueError, TypeError):
            return JsonResponse({'error': 'IDs must be valid integers.'}, status=400)

        if item_type == 'genre':
            count, _ = Genre.objects.filter(id__in=int_ids).delete()
        elif item_type == 'language':
            count, _ = Language.objects.filter(id__in=int_ids).delete()
        elif item_type == 'cast':
            count, _ = CastMember.objects.filter(id__in=int_ids).delete()
        else:
            return JsonResponse({'error': 'Invalid item_type specified.'}, status=400)

        return JsonResponse({'status': 'ok', 'count': count, 'deleted_ids': int_ids, 'item_type': item_type})
    return JsonResponse({'error': 'Invalid method'}, status=405)


@staff_or_admin_required
def api_unassign_movie_taxonomy(request, movie_id):
    """
    POST /movies/manage/api/movie/<movie_id>/unassign-taxonomy/
    Unassigns/removes a Genre, Language, or Cast member from THIS specific movie
    WITHOUT deleting the underlying reference entity from the database.
    """
    if request.method == 'POST':
        movie = get_object_or_404(Movie, id=movie_id)
        try:
            if request.content_type == 'application/json':
                import json
                data = json.loads(request.body)
                item_type = data.get('item_type')
                item_id = data.get('id')
            else:
                item_type = request.POST.get('item_type')
                item_id = request.POST.get('id')
        except Exception:
            return JsonResponse({'error': 'Invalid payload'}, status=400)

        if not item_type or not item_id:
            return JsonResponse({'error': 'item_type and id are required.'}, status=400)

        try:
            target_id = int(item_id)
        except (ValueError, TypeError):
            return JsonResponse({'error': 'Invalid ID integer.'}, status=400)

        if item_type == 'genre':
            movie.genres.remove(target_id)
        elif item_type == 'language':
            movie.languages.remove(target_id)
        elif item_type == 'cast':
            movie.cast.remove(target_id)
        else:
            return JsonResponse({'error': 'Invalid item_type specified.'}, status=400)

        return JsonResponse({'status': 'ok', 'movie_id': movie_id, 'unassigned_id': target_id, 'item_type': item_type})
    return JsonResponse({'error': 'Invalid method'}, status=405)


@staff_or_admin_required
def api_get_theater_screens(request, theater_id):
    """
    GET /movies/manage/api/theater/<theater_id>/screens/
    Returns a JSON list of screens belonging ONLY to the specified theater.
    """
    theater = get_object_or_404(Theater, id=theater_id)
    screens = Screen.objects.filter(theater=theater).order_by('name')
    data = [
        {
            'id': s.id,
            'name': s.name,
            'screen_type': s.get_screen_type_display(),
            'capacity': s.total_seats
        }
        for s in screens
    ]
    return JsonResponse({'status': 'ok', 'theater_id': theater_id, 'screens': data})







@user_passes_test(is_staff_user, login_url='/login/')
def admin_movie_delete(request, movie_id):
    movie = get_object_or_404(Movie, id=movie_id)
    if request.method == 'POST':
        title = movie.title
        movie.delete()
        messages.success(request, f'Movie "{title}" deleted.')
        return redirect('admin_manage_movies')
    return render(request, 'movies/custom_admin/confirm_delete.html', {
        'object': movie,
        'type': 'Movie',
        'cancel_url': 'admin_manage_movies'
    })


@user_passes_test(is_staff_user, login_url='/login/')
def admin_manage_theaters(request):
    search = request.GET.get('search', '').strip()
    theaters = (
        Theater.objects
        .annotate(
            screens_count=Count('screens', distinct=True),
            schedules_count=Count('schedules', distinct=True),
        )
        .order_by('name')
    )
    if search:
        theaters = theaters.filter(Q(name__icontains=search) | Q(location__icontains=search))

    return render(request, 'movies/custom_admin/manage_theaters.html', {
        'theaters': theaters,
        'search': search,
    })


@user_passes_test(is_staff_user, login_url='/login/')
def admin_theater_form(request, theater_id=None):
    theater = get_object_or_404(Theater, id=theater_id) if theater_id else None
    if request.method == 'POST':
        form = TheaterForm(request.POST, instance=theater)
        if form.is_valid():
            saved_theater = form.save()
            messages.success(request, f'Theater "{saved_theater.name}" saved successfully.')
            return redirect('admin_manage_theaters')
    else:
        form = TheaterForm(instance=theater)

    return render(request, 'movies/custom_admin/theater_form.html', {
        'form': form,
        'theater': theater,
    })


@user_passes_test(is_staff_user, login_url='/login/')
def admin_theater_delete(request, theater_id):
    theater = get_object_or_404(Theater, id=theater_id)
    if request.method == 'POST':
        name = theater.name
        theater.delete()
        messages.success(request, f'Theater "{name}" and connected screens/schedules deleted.')
        return redirect('admin_manage_theaters')
    return render(request, 'movies/custom_admin/confirm_delete.html', {
        'object': theater,
        'type': 'Theater',
        'cancel_url': 'admin_manage_theaters'
    })


@user_passes_test(is_staff_user, login_url='/login/')
def admin_manage_schedules(request):
    if request.method == 'POST':
        schedule_ids = request.POST.getlist('schedule_ids')
        if schedule_ids:
            deleted_count, _ = ShowSchedule.objects.filter(id__in=schedule_ids).delete()
            messages.success(request, f'Successfully deleted {deleted_count} show schedule(s).')
        else:
            messages.warning(request, 'No show schedules were selected for deletion.')
        return redirect('admin_manage_schedules')

    schedules = ShowSchedule.objects.select_related('movie', 'theater', 'screen').order_by('-show_time')
    return render(request, 'movies/custom_admin/manage_schedules.html', {
        'schedules': schedules,
    })


@user_passes_test(is_staff_user, login_url='/login/')
def admin_schedule_form(request, schedule_id=None):
    schedule = get_object_or_404(ShowSchedule, id=schedule_id) if schedule_id else None
    if request.method == 'POST':
        form = ShowScheduleForm(request.POST, instance=schedule)
        if form.is_valid():
            try:
                form.save()
                messages.success(request, 'Show schedule saved successfully.')
                return redirect('admin_manage_schedules')
            except Exception as e:
                messages.error(request, f'Database Error saving schedule: {str(e)}')
    else:
        form = ShowScheduleForm(instance=schedule)

    return render(request, 'movies/custom_admin/schedule_form.html', {
        'form': form,
        'schedule': schedule,
    })



@user_passes_test(is_staff_user, login_url='/login/')
def admin_schedule_delete(request, schedule_id):
    schedule = get_object_or_404(ShowSchedule, id=schedule_id)
    if request.method == 'POST':
        schedule.delete()
        messages.success(request, 'Schedule deleted.')
        return redirect('admin_manage_schedules')
    return render(request, 'movies/custom_admin/confirm_delete.html', {
        'object': schedule,
        'type': 'Show Schedule',
        'cancel_url': 'admin_manage_schedules'
    })


@user_passes_test(is_staff_user, login_url='/login/')
def admin_manage_reports(request):
    reports = ReportedReview.objects.select_related('review', 'review__movie', 'review__user', 'reported_by')
    return render(request, 'movies/custom_admin/manage_reports.html', {
        'reports': reports,
    })


@user_passes_test(is_staff_user, login_url='/login/')
def admin_resolve_report(request, report_id):
    report = get_object_or_404(ReportedReview, id=report_id)
    action = request.POST.get('action')

    if request.method == 'POST' and action:
        if action == 'hide_review':
            report.review.is_active = False
            report.review.save()
            report.review.movie.update_rating()
            report.status = 'resolved'
            report.reviewed_by = request.user
            report.reviewed_at = timezone.now()
            report.save()
            messages.success(request, 'Review hidden and report resolved.')
        elif action == 'dismiss':
            report.status = 'dismissed'
            report.reviewed_by = request.user
            report.reviewed_at = timezone.now()
            report.save()
            messages.info(request, 'Report dismissed.')
        elif action == 'restore_review':
            report.review.is_active = True
            report.review.save()
            report.review.movie.update_rating()
            report.status = 'dismissed'
            report.reviewed_by = request.user
            report.reviewed_at = timezone.now()
            report.save()
            messages.success(request, 'Review restored and report dismissed.')

    return redirect('admin_manage_reports')


@staff_or_admin_required
def admin_manage_taxonomies(request):
    genres = Genre.objects.all()
    languages = Language.objects.all()
    cast_members = CastMember.objects.all()
    theaters = Theater.objects.all()

    genre_form = GenreForm(prefix='genre')
    language_form = LanguageForm(prefix='lang')
    cast_form = CastMemberForm(prefix='cast')
    theater_form = TheaterForm(prefix='theater')

    if request.method == 'POST':
        item_type = request.POST.get('item_type')

        # Creation Handlers
        if item_type == 'genre':
            genre_form = GenreForm(request.POST, prefix='genre')
            if genre_form.is_valid():
                g = genre_form.save()
                messages.success(request, f'Genre "{g.name}" added successfully.')
                return redirect('admin_manage_taxonomies')
        elif item_type == 'language':
            language_form = LanguageForm(request.POST, prefix='lang')
            if language_form.is_valid():
                l = language_form.save()
                messages.success(request, f'Language "{l.name}" added successfully.')
                return redirect('admin_manage_taxonomies')
        elif item_type == 'cast':
            cast_form = CastMemberForm(request.POST, request.FILES, prefix='cast')
            if cast_form.is_valid():
                c = cast_form.save()
                messages.success(request, f'Cast member "{c.name}" added successfully.')
                return redirect('admin_manage_taxonomies')
        elif item_type == 'theater':
            theater_form = TheaterForm(request.POST, prefix='theater')
            if theater_form.is_valid():
                t = theater_form.save()
                messages.success(request, f'Theater "{t.name}" added successfully.')
                return redirect('admin_manage_taxonomies')

        # Deletion Handlers
        elif item_type == 'delete_genre':
            genre_id = request.POST.get('genre_id')
            g = get_object_or_404(Genre, id=genre_id)
            g_name = g.name
            g.delete()
            messages.success(request, f'Genre "{g_name}" deleted successfully.')
            return redirect('admin_manage_taxonomies')
        elif item_type == 'delete_language':
            lang_id = request.POST.get('language_id')
            l = get_object_or_404(Language, id=lang_id)
            l_name = l.name
            l.delete()
            messages.success(request, f'Language "{l_name}" deleted successfully.')
            return redirect('admin_manage_taxonomies')
        elif item_type == 'delete_cast':
            cast_id = request.POST.get('cast_id')
            c = get_object_or_404(CastMember, id=cast_id)
            c_name = c.name
            c.delete()
            messages.success(request, f'Cast member "{c_name}" deleted successfully.')
            return redirect('admin_manage_taxonomies')
        elif item_type == 'delete_theater':
            t_id = request.POST.get('theater_id')
            t = get_object_or_404(Theater, id=t_id)
            t_name = t.name
            t.delete()
            messages.success(request, f'Theater "{t_name}" deleted successfully.')
            return redirect('admin_manage_taxonomies')

    return render(request, 'movies/custom_admin/manage_taxonomies.html', {
        'genres': genres,
        'languages': languages,
        'cast_members': cast_members,
        'theaters': theaters,
        'genre_form': genre_form,
        'language_form': language_form,
        'cast_form': cast_form,
        'theater_form': theater_form,
    })



@user_passes_test(is_staff_user, login_url='/login/')
def admin_manage_screens(request):
    theater_id = request.GET.get('theater_id')
    screens = Screen.objects.select_related('theater').annotate(seat_count=Count('seats', distinct=True)).order_by('theater__name', 'name')
    selected_theater = None
    if theater_id:
        selected_theater = get_object_or_404(Theater, id=theater_id)
        screens = screens.filter(theater=selected_theater)

    return render(request, 'movies/custom_admin/manage_screens.html', {
        'screens': screens,
        'selected_theater': selected_theater,
        'theaters': Theater.objects.order_by('name'),
    })


@user_passes_test(is_staff_user, login_url='/login/')
def admin_screen_form(request, screen_id=None):
    screen = get_object_or_404(Screen, id=screen_id) if screen_id else None
    theater_id = request.GET.get('theater_id')

    if request.method == 'POST':
        form = ScreenForm(request.POST, instance=screen)
        if form.is_valid():
            saved_screen = form.save()
            saved_screen.generate_seats()
            messages.success(request, f'Screen "{saved_screen.name}" saved and seat grid generated ({saved_screen.total_seats} seats).')
            return redirect(f"{reverse('admin_manage_screens')}?theater_id={saved_screen.theater.id}")
    else:
        initial = {'theater': theater_id} if (theater_id and not screen) else {}
        form = ScreenForm(instance=screen, initial=initial)

    return render(request, 'movies/custom_admin/screen_form.html', {
        'form': form,
        'screen': screen,
    })


@user_passes_test(is_staff_user, login_url='/login/')
def admin_screen_delete(request, screen_id):
    screen = get_object_or_404(Screen, id=screen_id)
    if request.method == 'POST':
        name = screen.name
        screen.delete()
        messages.success(request, f'Screen "{name}" deleted.')
        return redirect('admin_manage_screens')
    return render(request, 'movies/custom_admin/confirm_delete.html', {
        'object': screen,
        'type': 'Screen',
        'cancel_url': 'admin_manage_screens'
    })


@user_passes_test(is_staff_user, login_url='/login/')
def admin_screen_seat_map(request, screen_id):
    screen = get_object_or_404(Screen, id=screen_id)
    screen.generate_seats()

    if request.method == 'POST':
        seat_id = request.POST.get('seat_id')
        action = request.POST.get('action')
        seat = get_object_or_404(Seat, id=seat_id, screen=screen)

        if action == 'toggle_active':
            seat.is_active = not seat.is_active
            seat.save(update_fields=['is_active'])
            messages.success(request, f'Seat {seat.seat_number} maintenance status toggled.')
        elif action == 'change_tier':
            new_tier = request.POST.get('seat_type')
            if new_tier in ['regular', 'premium', 'recliner']:
                seat.seat_type = new_tier
                mult_map = {'regular': 1.00, 'premium': 1.20, 'recliner': 1.50}
                seat.price_multiplier = mult_map[new_tier]
                seat.save(update_fields=['seat_type', 'price_multiplier'])
                messages.success(request, f'Seat {seat.seat_number} tier changed to {seat.get_seat_type_display()}.')

        return redirect('admin_screen_seat_map', screen_id=screen.id)

    seats = screen.seats.all().order_by('row', 'number')
    row_groups = {}
    for s in seats:
        row_groups.setdefault(s.row, []).append(s)

    return render(request, 'movies/custom_admin/screen_seat_map.html', {
        'screen': screen,
        'row_groups': row_groups,
    })


@user_passes_test(is_staff_user, login_url='/login/')
def admin_manage_seats(request):
    theaters = Theater.objects.all()
    theater_id = request.GET.get('theater_id')
    screen_id = request.GET.get('screen_id')
    schedule_id = request.GET.get('schedule_id')

    selected_screen = None
    selected_theater = None

    if theater_id:
        selected_theater = Theater.objects.filter(id=theater_id).first()

    if screen_id:
        screen_obj = Screen.objects.filter(id=screen_id).first()
        if screen_obj:
            if not selected_theater or selected_theater.id == screen_obj.theater_id:
                selected_theater = screen_obj.theater
                selected_screen = screen_obj

    if not selected_theater:
        selected_theater = Theater.objects.filter(screens__isnull=False).distinct().first()
        if not selected_theater:
            selected_theater = theaters.first()

    screens = selected_theater.screens.all() if selected_theater else Screen.objects.none()

    if selected_theater and (not selected_screen or selected_screen.theater_id != selected_theater.id):
        selected_screen = screens.first()

    schedules = ShowSchedule.objects.filter(theater=selected_theater).order_by('-show_time') if selected_theater else ShowSchedule.objects.none()
    selected_schedule = schedules.filter(id=schedule_id).first() if schedule_id else None

    row_groups = {}
    total_count = 0
    available_count = 0
    booked_count = 0
    maintenance_count = 0

    if selected_screen:
        selected_screen.generate_seats()
        seats = selected_screen.seats.all().order_by('row', 'number')

        booked_seat_ids = set()
        if selected_schedule:
            booked_seat_ids = set(
                BookingSeat.objects.filter(show_schedule=selected_schedule)
                .values_list('seat_id', flat=True)
            )
        else:
            booked_seat_ids = set(Seat.objects.filter(screen=selected_screen, is_booked=True).values_list('id', flat=True))

        for s in seats:
            total_count += 1
            if not s.is_active:
                s.display_status = 'maintenance'
                maintenance_count += 1
            elif s.id in booked_seat_ids or s.is_booked:
                s.display_status = 'booked'
                booked_count += 1
            else:
                s.display_status = 'available'
                available_count += 1

            row_groups.setdefault(s.row, []).append(s)

    return render(request, 'movies/custom_admin/manage_seats.html', {
        'theaters': theaters,
        'selected_theater': selected_theater,
        'screens': screens,
        'selected_screen': selected_screen,
        'schedules': schedules,
        'selected_schedule': selected_schedule,
        'row_groups': row_groups,
        'total_count': total_count,
        'available_count': available_count,
        'booked_count': booked_count,
        'maintenance_count': maintenance_count,
    })


@user_passes_test(is_staff_user, login_url='/login/')
def admin_update_seat_status(request):
    if request.method == 'POST':
        action_type = request.POST.get('action_type')
        screen_id = request.POST.get('screen_id')
        schedule_id = request.POST.get('schedule_id')
        screen = get_object_or_404(Screen, id=screen_id)

        schedule = ShowSchedule.objects.filter(id=schedule_id).first() if schedule_id else None

        if action_type == 'single_seat':
            seat_id = request.POST.get('seat_id')
            new_status = request.POST.get('status')
            new_tier = request.POST.get('seat_type')
            seat = get_object_or_404(Seat, id=seat_id, screen=screen)

            if new_status == 'available':
                seat.is_active = True
                seat.is_booked = False
                seat.save(update_fields=['is_active', 'is_booked'])
                if schedule:
                    BookingSeat.objects.filter(show_schedule=schedule, seat=seat).delete()
                messages.success(request, f'Seat {seat.seat_number} marked as Available (Unbooked).')

            elif new_status == 'booked':
                seat.is_active = True
                seat.is_booked = True
                seat.save(update_fields=['is_active', 'is_booked'])
                if schedule:
                    admin_booking, _ = Booking.objects.get_or_create(
                        user=request.user,
                        show_schedule=schedule,
                        status='confirmed',
                        defaults={'number_of_seats': 1, 'total_price': schedule.price, 'movie': schedule.movie, 'theater': schedule.theater}
                    )
                    BookingSeat.objects.get_or_create(
                        booking=admin_booking,
                        show_schedule=schedule,
                        seat=seat,
                        defaults={'price': seat.calculate_price(schedule.price)}
                    )
                messages.success(request, f'Seat {seat.seat_number} marked as Booked / Reserved.')

            elif new_status == 'maintenance':
                seat.is_active = False
                seat.save(update_fields=['is_active'])
                messages.warning(request, f'Seat {seat.seat_number} marked as Out of Service (Maintenance).')

            if new_tier in ['regular', 'premium', 'recliner']:
                seat.seat_type = new_tier
                mult_map = {'regular': 1.00, 'premium': 1.20, 'recliner': 1.50}
                seat.price_multiplier = mult_map[new_tier]
                seat.save(update_fields=['seat_type', 'price_multiplier'])

        elif action_type == 'bulk_row':
            row_letter = request.POST.get('row_letter')
            row_status = request.POST.get('row_status')
            seats_in_row = screen.seats.filter(row=row_letter)

            if row_status == 'available':
                seats_in_row.update(is_active=True, is_booked=False)
                if schedule:
                    BookingSeat.objects.filter(show_schedule=schedule, seat__in=seats_in_row).delete()
                messages.success(request, f'All seats in Row {row_letter} marked as Available.')

            elif row_status == 'booked':
                seats_in_row.update(is_active=True, is_booked=True)
                if schedule:
                    admin_booking, _ = Booking.objects.get_or_create(
                        user=request.user,
                        show_schedule=schedule,
                        status='confirmed',
                        defaults={'number_of_seats': 1, 'total_price': schedule.price, 'movie': schedule.movie, 'theater': schedule.theater}
                    )
                    for s in seats_in_row:
                        BookingSeat.objects.get_or_create(
                            booking=admin_booking,
                            show_schedule=schedule,
                            seat=s,
                            defaults={'price': s.calculate_price(schedule.price)}
                        )
                messages.success(request, f'All seats in Row {row_letter} marked as Booked / Reserved.')

            elif row_status == 'maintenance':
                seats_in_row.update(is_active=False)
                messages.warning(request, f'All seats in Row {row_letter} marked as Out of Service.')

        elif action_type == 'reset_screen':
            screen.seats.all().update(is_active=True, is_booked=False)
            if schedule:
                BookingSeat.objects.filter(show_schedule=schedule).delete()
        # Dynamically auto-sync schedule available seats with real active unbooked physical seats
        if schedule:
            booked_count = BookingSeat.objects.filter(show_schedule=schedule).count()
            active_seats_count = screen.seats.filter(is_active=True).count()
            schedule.available_seats = max(0, active_seats_count - booked_count)
            schedule.save(update_fields=['available_seats'])

        redirect_url = f"{reverse('admin_manage_seats')}?theater_id={screen.theater.id}&screen_id={screen.id}"


        if schedule_id:
            redirect_url += f"&schedule_id={schedule_id}"
        return redirect(redirect_url)

    return redirect('admin_manage_seats')


@user_passes_test(is_staff_user, login_url='/login/')
def admin_bulk_schedule_add(request):
    movies = Movie.objects.all()
    screens = Screen.objects.select_related('theater').all()
    theaters = Theater.objects.all()

    if request.method == 'POST':
        movie_id = request.POST.get('movie_id')
        screen_id = request.POST.get('screen_id')
        start_date_str = request.POST.get('start_date')
        end_date_str = request.POST.get('end_date')
        time_slots_raw = request.POST.get('time_slots', '')  # e.g., '10:00, 14:00, 18:00, 21:30'
        price = request.POST.get('price', 200.00)

        movie = get_object_or_404(Movie, id=movie_id)
        screen = get_object_or_404(Screen, id=screen_id)

        from datetime import datetime
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
            times = [t.strip() for t in time_slots_raw.split(',') if t.strip()]

            created_count = 0
            current_date = start_date
            while current_date <= end_date:
                for time_str in times:
                    try:
                        time_obj = datetime.strptime(time_str, '%H:%M').time()
                        dt = timezone.make_aware(datetime.combine(current_date, time_obj))
                        # Create schedule if not duplicate
                        sch, created = ShowSchedule.objects.get_or_create(
                            movie=movie,
                            theater=screen.theater,
                            screen=screen,
                            show_time=dt,
                            defaults={'price': price, 'available_seats': screen.total_seats}
                        )
                        if created:
                            created_count += 1
                    except ValueError:
                        pass
                current_date += timedelta(days=1)

            messages.success(request, f'Successfully generated {created_count} show schedule(s) for "{movie.title}" on {screen.name}.')
            return redirect('admin_manage_schedules')
        except Exception as e:
            messages.error(request, f'Error generating schedules: {str(e)}')

    return render(request, 'movies/custom_admin/bulk_schedule_form.html', {
        'movies': movies,
        'screens': screens,
        'theaters': theaters,
    })


@user_passes_test(is_staff_user, login_url='/login/')
def admin_manage_bookings(request):
    search = request.GET.get('search', '').strip()
    status_filter = request.GET.get('status', '').strip()

    bookings = Booking.objects.select_related('user', 'show_schedule__movie', 'show_schedule__theater', 'show_schedule__screen', 'movie', 'theater').prefetch_related('booked_seats__seat').all()

    if search:
        bookings = bookings.filter(
            Q(booking_reference__icontains=search) |
            Q(user__username__icontains=search) |
            Q(user__email__icontains=search) |
            Q(show_schedule__movie__title__icontains=search)
        )

    if status_filter:
        bookings = bookings.filter(status=status_filter)

    return render(request, 'movies/custom_admin/manage_bookings.html', {
        'bookings': bookings,
        'search': search,
        'status_filter': status_filter,
    })


@user_passes_test(is_staff_user, login_url='/login/')
def admin_booking_action(request, booking_id):
    booking = get_object_or_404(Booking, id=booking_id)
    action = request.POST.get('action')

    if request.method == 'POST':
        try:
            if action == 'confirm' and booking.status == 'pending':
                booking.confirm_booking()
                messages.success(request, f'Booking {booking.booking_reference} confirmed.')
            elif action == 'cancel':
                booking.cancel_booking()
                messages.success(request, f'Booking {booking.booking_reference} cancelled and seats restored.')
        except Exception as e:
            messages.error(request, f'Booking action failed: {str(e)}')

    return redirect('admin_manage_bookings')


@user_passes_test(is_staff_user, login_url='/login/')
def admin_movie_gallery(request, movie_id):
    movie = get_object_or_404(Movie, id=movie_id)
    if request.method == 'POST':
        if 'add_image' in request.POST:
            image_files = request.FILES.getlist('image')
            caption = request.POST.get('caption', '')
            if image_files:
                count = 0
                for img_file in image_files:
                    MovieImage.objects.create(movie=movie, image=img_file, caption=caption or f"Poster {movie.images.count() + 1}")
                    count += 1
                messages.success(request, f'{count} gallery poster(s) added successfully.')
        elif 'delete_image_id' in request.POST:
            image_id = request.POST.get('delete_image_id')
            MovieImage.objects.filter(id=image_id, movie=movie).delete()
            messages.success(request, 'Gallery poster deleted.')

        return redirect('admin_movie_gallery', movie_id=movie.id)

    return render(request, 'movies/custom_admin/movie_gallery.html', {
        'movie': movie,
        'images': movie.images.all(),
    })


@user_passes_test(is_staff_user, login_url='/login/')
def admin_toggle_seat_ajax(request):
    if request.method == 'POST':
        seat_id = request.POST.get('seat_id')
        screen_id = request.POST.get('screen_id')
        schedule_id = request.POST.get('schedule_id')
        target_status = request.POST.get('target_status')

        seat = get_object_or_404(Seat.objects.select_related('screen'), id=seat_id, screen_id=screen_id)
        screen = seat.screen
        schedule = ShowSchedule.objects.filter(id=schedule_id).first() if schedule_id else None

        # Determine new status
        if not target_status:
            if not seat.is_active:
                new_status = 'available'
            elif seat.is_booked or (schedule and BookingSeat.objects.filter(show_schedule=schedule, seat=seat).exists()):
                new_status = 'available'
            else:
                new_status = 'booked'
        else:
            new_status = target_status

        if new_status == 'available':
            seat.is_active = True
            seat.is_booked = False
            seat.save(update_fields=['is_active', 'is_booked'])
            if schedule:
                BookingSeat.objects.filter(show_schedule=schedule, seat=seat).delete()

        elif new_status == 'booked':
            seat.is_active = True
            seat.is_booked = True
            seat.save(update_fields=['is_active', 'is_booked'])
            if schedule:
                admin_booking, _ = Booking.objects.get_or_create(
                    user=request.user,
                    show_schedule=schedule,
                    status='confirmed',
                    defaults={'number_of_seats': 1, 'total_price': schedule.price, 'movie': schedule.movie, 'theater': schedule.theater}
                )
                BookingSeat.objects.get_or_create(
                    booking=admin_booking,
                    show_schedule=schedule,
                    seat=seat,
                    defaults={'price': seat.calculate_price(schedule.price)}
                )

        elif new_status == 'maintenance':
            seat.is_active = False
            seat.save(update_fields=['is_active'])

        # Recalculate schedule capacity and counts in lightweight queries
        total_count = screen.seats.count()
        maintenance_count = screen.seats.filter(is_active=False).count()

        if schedule:
            booked_count = BookingSeat.objects.filter(show_schedule=schedule).count()
            active_count = total_count - maintenance_count
            schedule_available_seats = max(0, active_count - booked_count)
            ShowSchedule.objects.filter(id=schedule.id).update(available_seats=schedule_available_seats)
            available_count = schedule_available_seats
        else:
            booked_count = screen.seats.filter(is_active=True, is_booked=True).count()
            available_count = total_count - maintenance_count - booked_count
            schedule_available_seats = 0

        return JsonResponse({
            'success': True,
            'seat_id': seat.id,
            'new_status': new_status,
            'seat_number': seat.seat_number,
            'total_count': total_count,
            'available_count': available_count,
            'booked_count': booked_count,
            'maintenance_count': maintenance_count,
            'schedule_available_seats': schedule_available_seats,
        })

    return JsonResponse({'success': False, 'error': 'Invalid request method.'}, status=400)


from django.http import HttpResponse, Http404, HttpResponseForbidden
from django.shortcuts import get_object_or_404, render
from .ticket_service import generate_and_save_ticket


@login_required(login_url='/login/')
def download_ticket_pdf(request, booking_reference):
    """
    Serve the PDF ticket directly with application/pdf headers.
    Streams PDF bytes directly (using stored ticket or generating on-the-fly)
    to prevent broken Cloudinary 302 redirects and ensure clean downloads.
    """
    booking = get_object_or_404(
        Booking.objects.select_related('user', 'movie', 'theater', 'show_schedule__movie', 'show_schedule__theater', 'show_schedule__screen'),
        booking_reference=booking_reference
    )

    if booking.user != request.user and not request.user.is_staff:
        return HttpResponseForbidden("You do not have permission to view this ticket.")

    if booking.status != 'confirmed':
        return HttpResponse("Ticket is only available for confirmed bookings.", status=400)

    try:
        from .ticket_service import get_booking_ticket_bytes
        pdf_bytes = get_booking_ticket_bytes(booking)
        if not pdf_bytes:
            return HttpResponse("Ticket generation failed. Please try again in a moment.", status=503)

        response = HttpResponse(pdf_bytes, content_type='application/pdf')
        filename = f"ticket_{booking.booking_reference}.pdf"
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response
    except Exception as e:
        logger.error(f"Error serving ticket PDF for {booking_reference}: {e}", exc_info=True)
        return HttpResponse(f"Error serving ticket: {str(e)}", status=500)


def verify_ticket(request, booking_reference):
    """
    Server-side ticket verification endpoint that accepts a QR code ticket identifier.
    Returns basic verification details (Movie, Theater, Screen, Show, Seats, Booking ID, Status).
    Considers ticket valid ONLY when booking exists and status is confirmed/completed.
    Supports both HTML UI rendering and JSON response format.
    """
    from django.http import JsonResponse

    booking = Booking.objects.select_related(
        'user', 'movie', 'theater',
        'show_schedule__movie', 'show_schedule__theater', 'show_schedule__screen'
    ).prefetch_related('booked_seats__seat').filter(booking_reference=booking_reference).first()

    is_valid = bool(booking and booking.status in ['confirmed', 'completed'])

    movie_title = "—"
    theater_name = "—"
    screen_name = "—"
    show_time_str = "—"
    seats_str = "—"
    status_display = "Invalid"

    if booking:
        status_display = booking.get_status_display()
        movie = booking.movie or (booking.show_schedule.movie if booking.show_schedule else None)
        theater = booking.theater or (booking.show_schedule.theater if booking.show_schedule else None)
        screen = booking.show_schedule.screen if (booking.show_schedule and booking.show_schedule.screen) else None

        movie_title = movie.title if movie else "—"
        theater_name = theater.name if theater else "—"
        screen_name = screen.name if screen else "Screen 1"

        if booking.show_schedule and booking.show_schedule.show_time:
            show_time_str = booking.show_schedule.show_time.strftime("%I:%M %p (%d %b %Y)")
        else:
            show_time_str = booking.booked_at.strftime("%I:%M %p (%d %b %Y)")

        booked_seats_qs = booking.booked_seats.all()
        if booked_seats_qs.exists():
            seats_str = ", ".join([bs.seat.seat_number for bs in booked_seats_qs])
        elif booking.seat:
            seats_str = booking.seat.seat_number
        else:
            seats_str = f"{booking.number_of_seats} seat(s)"

    # Handle JSON API request format
    wants_json = request.GET.get('format') == 'json' or request.headers.get('Accept') == 'application/json'
    if wants_json:
        if is_valid:
            return JsonResponse({
                'valid': True,
                'booking_id': booking.booking_reference,
                'movie': movie_title,
                'theater': theater_name,
                'screen': screen_name,
                'show_time': show_time_str,
                'seats': seats_str,
                'status': status_display,
            })
        else:
            return JsonResponse({
                'valid': False,
                'booking_id': booking_reference,
                'status': status_display,
                'error': 'Invalid, cancelled, or non-existent ticket identifier.',
            }, status=400)

    payment = None
    if booking:
        from .models import Payment
        payment = Payment.objects.filter(booking=booking, status='success').first()

    context = {
        'booking': booking,
        'is_valid': is_valid,
        'payment': payment,
        'booking_reference': booking_reference,
        'movie_title': movie_title,
        'theater_name': theater_name,
        'screen_name': screen_name,
        'show_time_str': show_time_str,
        'seats_str': seats_str,
        'status_display': status_display,
    }
    return render(request, 'movies/ticket_verify.html', context)


@login_required(login_url='/login/')
def resend_booking_email(request, booking_reference):
    """
    User/Admin endpoint to manually trigger or retry sending ticket confirmation email.
    """
    booking = get_object_or_404(Booking, booking_reference=booking_reference)
    if booking.user != request.user and not request.user.is_staff:
        return HttpResponseForbidden("You do not have permission to resend this ticket email.")

    if booking.status != 'confirmed':
        messages.error(request, "Ticket emails can only be sent for confirmed bookings.")
        return redirect('profile')

    from .tasks import send_ticket_email_task
    # Reset tracking status to allow fresh email dispatch attempt
    booking.email_status = 'pending'
    booking.email_attempts = 0
    booking.email_last_error = None
    booking.save(update_fields=['email_status', 'email_attempts', 'email_last_error'])

    success = send_ticket_email_task(booking.id)
    booking.refresh_from_db()

    if success or booking.email_status == 'sent':
        messages.success(request, f"Ticket email dispatched to {booking.user.email}!")
    else:
        err = booking.email_last_error or "Unknown error"
        messages.error(request, f"Failed to send ticket email: {err}")

    return redirect('profile')








