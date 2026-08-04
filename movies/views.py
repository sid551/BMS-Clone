from datetime import timedelta
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import JsonResponse


from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Q, Count
from django.utils import timezone
from .models import (
    Movie, MovieImage, Genre, Language, CastMember, Theater, Screen, Seat, ShowSeat, Booking,
    ShowSchedule, BookingSeat, Review, ReportedReview
)
from .forms import (
    ReviewForm, ReportReviewForm, MovieForm, GenreForm, LanguageForm,
    CastMemberForm, TheaterForm, ScreenForm, ShowScheduleForm
)




def is_staff_user(user):
    return user.is_authenticated and user.is_staff



def movie_list(request):
    search_query = request.GET.get('search')
    movies = Movie.objects.prefetch_related('genres', 'languages')
    if search_query:
        movies = movies.filter(title__icontains=search_query)
    return render(request, 'movies/movie_list.html', {'movies': movies})


def theater_list(request, movie_id):
    movie = get_object_or_404(Movie, id=movie_id)
    schedules = ShowSchedule.objects.filter(movie=movie).select_related('theater', 'screen')
    theaters = Theater.objects.filter(schedules__movie=movie).distinct().prefetch_related('screens')
    return render(request, 'movies/theater_list.html', {
        'movie': movie,
        'theaters': theaters,
        'schedules': schedules,
    })


@login_required(login_url='/login/')
def book_seats(request, theater_id):
    theater = get_object_or_404(Theater, id=theater_id)
    schedule_id = request.GET.get('schedule_id')

    if schedule_id:
        schedule = get_object_or_404(ShowSchedule, id=schedule_id, theater=theater)
    else:
        schedule = ShowSchedule.objects.filter(theater=theater, show_time__gte=timezone.now()).first()

    screen = schedule.screen if schedule and schedule.screen else theater.screens.first()

    if screen:
        screen.generate_seats()
        all_seats = Seat.objects.filter(screen=screen, is_active=True).order_by('row', 'number')
        if schedule and schedule.screen and not schedule.show_seats.exists():
            schedule._generate_show_seats()
    else:
        all_seats = Seat.objects.filter(theater=theater, is_active=True).order_by('seat_number')

    # Get already booked or active reserved seat IDs for this specific schedule
    booked_seat_ids = set()
    confirmed_booked_ids = set()
    if schedule:
        from .reservation_service import _release_stale
        _release_stale(schedule.id)

        now = timezone.now()
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
    else:
        booked_seat_ids = set(Seat.objects.filter(theater=theater, is_booked=True).values_list('id', flat=True))

    # Base ticket price
    base_price = schedule.price if schedule else 200.00

    # Group seats by tier and row for layout rendering (Regular front near screen -> Premium -> Recliner back)
    tier_groups = {'regular': {}, 'premium': {}, 'recliner': {}}

    tier_prices = {}

    for s in all_seats:
        s.is_already_booked = s.id in booked_seat_ids
        s.calculated_price = round(float(base_price) * float(s.price_multiplier), 2)
        tier_prices[s.seat_type] = s.calculated_price

        tier = s.seat_type if s.seat_type in tier_groups else 'regular'
        row_dict = tier_groups[tier]
        row_dict.setdefault(s.row, []).append(s)

    if request.method == 'POST':
        selected_seat_ids = [int(sid) for sid in request.POST.getlist('seats') if sid.isdigit()]

        if not selected_seat_ids:
            return render(request, 'movies/seat_selection.html', {
                'theaters': theater,
                'schedule': schedule,
                'screen': screen,
                'tier_groups': tier_groups,
                'tier_prices': tier_prices,
                'error': 'Please select at least one seat to proceed.',
            })

        # Validate available seats
        number_of_seats = len(selected_seat_ids)

        if schedule and number_of_seats > schedule.available_seats:
            return render(request, 'movies/seat_selection.html', {
                'theaters': theater,
                'schedule': schedule,
                'screen': screen,
                'tier_groups': tier_groups,
                'tier_prices': tier_prices,
                'error': f'Not enough seats available. Only {schedule.available_seats} left.',
            })

        # Ensure none of selected seats are already booked or held by another user
        now = timezone.now()
        currently_held_by_others = set(
            ShowSeat.objects.filter(
                show_schedule=schedule,
                seat_id__in=selected_seat_ids,
                status__in=['booked', 'reserved'],
                reserved_until__gt=now
            )
            .exclude(reserved_by=request.user)
            .values_list('seat_id', flat=True)
        )
        already_booked = (set(selected_seat_ids) & confirmed_booked_ids) | currently_held_by_others
        if already_booked:
            return render(request, 'movies/seat_selection.html', {
                'theaters': theater,
                'schedule': schedule,
                'screen': screen,
                'tier_groups': tier_groups,
                'tier_prices': tier_prices,
                'error': 'One or more of your selected seats are currently reserved or booked by another user. Please choose available seats.',
            })

        # Calculate exact total price per selected seat
        chosen_seats = Seat.objects.filter(id__in=selected_seat_ids)
        total_calculated_price = sum(s.calculate_price(base_price) for s in chosen_seats)

        from django.db import transaction
        with transaction.atomic():
            booking = Booking.objects.create(
                user=request.user,
                show_schedule=schedule,
                number_of_seats=number_of_seats,
                total_price=total_calculated_price,
                status='confirmed',
                movie=schedule.movie if schedule else theater.movie,
                theater=theater,
            )

            # Record BookingSeats
            booking_seats_to_create = []
            for s in chosen_seats:
                booking_seats_to_create.append(BookingSeat(
                    booking=booking,
                    show_schedule=schedule,
                    seat=s,
                    price=s.calculate_price(base_price)
                ))
            if booking_seats_to_create:
                BookingSeat.objects.bulk_create(booking_seats_to_create)

            # Update schedule available seats
            if schedule:
                schedule.available_seats = max(0, schedule.available_seats - number_of_seats)
                schedule.save(update_fields=['available_seats'])
                # Mark ShowSeat status as booked
                ShowSeat.objects.filter(show_schedule=schedule, seat_id__in=selected_seat_ids).update(
                    status='booked',
                    reserved_by=None,
                    reserved_until=None
                )

            # Mark seat as booked globally for fallback
            chosen_seats.update(is_booked=True)

        messages.success(
            request,
            f'Booking confirmed! Reference: {booking.booking_reference}. Seats: {", ".join(s.seat_number for s in chosen_seats)}'
        )
        return redirect('profile')

    return render(request, 'movies/seat_selection.html', {
        'theaters': theater,
        'schedule': schedule,
        'screen': screen,
        'tier_groups': tier_groups,
        'tier_prices': tier_prices,
        'seats': all_seats,
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

    # Clean up expired reservations before serving
    _expire_stale_reservations_by_id(schedule_id)

    show_seats = (
        schedule.show_seats
        .select_related('seat', 'reserved_by')
        .order_by('seat__row', 'seat__number')
    )

    current_user_id = request.user.id if request.user.is_authenticated else None

    rows = {}
    for ss in show_seats:
        seat = ss.seat
        row = seat.row
        is_mine = (ss.reserved_by_id == current_user_id and ss.status == 'reserved')
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
            'seconds_remaining': ss.seconds_remaining if is_mine else 0,
        })

    return JsonResponse({
        'schedule_id': schedule.id,
        'movie': schedule.movie.title,
        'theater': schedule.theater.name,
        'screen': schedule.screen.name if schedule.screen else None,
        'show_time': schedule.show_time.isoformat(),
        'base_price': float(schedule.price),
        'available_seats': schedule.available_seats,
        'rows': rows,
        'summary': {
            'total': show_seats.count(),
            'available': show_seats.filter(status='available').count(),
            'booked': show_seats.filter(status='booked').count(),
            'reserved': show_seats.filter(status='reserved').count(),
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
        return JsonResponse({'error': str(e)}, status=400)
    except Exception as e:
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

@user_passes_test(is_staff_user, login_url='/login/')
def admin_dashboard(request):
    movie_count = Movie.objects.count()
    genre_count = Genre.objects.count()
    language_count = Language.objects.count()
    cast_count = CastMember.objects.count()
    theater_count = Theater.objects.count()
    schedule_count = ShowSchedule.objects.count()
    pending_reports = ReportedReview.objects.filter(status='pending').count()

    recent_movies = Movie.objects.all()[:5]
    recent_reports = ReportedReview.objects.select_related('review', 'reported_by').filter(status='pending')[:5]

    return render(request, 'movies/custom_admin/dashboard.html', {
        'movie_count': movie_count,
        'genre_count': genre_count,
        'language_count': language_count,
        'cast_count': cast_count,
        'theater_count': theater_count,
        'schedule_count': schedule_count,
        'pending_reports': pending_reports,
        'recent_movies': recent_movies,
        'recent_reports': recent_reports,
    })


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


@user_passes_test(is_staff_user, login_url='/login/')
def admin_movie_form(request, movie_id=None):
    movie = get_object_or_404(Movie, id=movie_id) if movie_id else None
    if request.method == 'POST':
        form = MovieForm(request.POST, request.FILES, instance=movie)
        if form.is_valid():
            saved_movie = form.save()
            messages.success(request, f'Movie "{saved_movie.title}" saved successfully.')
            return redirect('admin_manage_movies')
    else:
        form = MovieForm(instance=movie)

    return render(request, 'movies/custom_admin/movie_form.html', {
        'form': form,
        'movie': movie,
    })


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


@user_passes_test(is_staff_user, login_url='/login/')
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
        if item_type == 'genre':
            genre_form = GenreForm(request.POST, prefix='genre')
            if genre_form.is_valid():
                genre_form.save()
                messages.success(request, 'Genre added.')
                return redirect('admin_manage_taxonomies')
        elif item_type == 'language':
            language_form = LanguageForm(request.POST, prefix='lang')
            if language_form.is_valid():
                language_form.save()
                messages.success(request, 'Language added.')
                return redirect('admin_manage_taxonomies')
        elif item_type == 'cast':
            cast_form = CastMemberForm(request.POST, request.FILES, prefix='cast')
            if cast_form.is_valid():
                cast_form.save()
                messages.success(request, 'Cast member added.')
                return redirect('admin_manage_taxonomies')
        elif item_type == 'theater':
            theater_form = TheaterForm(request.POST, prefix='theater')
            if theater_form.is_valid():
                theater_form.save()
                messages.success(request, 'Theater added.')
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

    selected_theater = Theater.objects.filter(id=theater_id).first() if theater_id else theaters.first()
    screens = selected_theater.screens.all() if selected_theater else Screen.objects.none()
    selected_screen = screens.filter(id=screen_id).first() if screen_id else screens.first()

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
            image_file = request.FILES.get('image')
            caption = request.POST.get('caption', '')
            if image_file:
                MovieImage.objects.create(movie=movie, image=image_file, caption=caption)
                messages.success(request, 'Gallery image added.')
        elif 'delete_image_id' in request.POST:
            image_id = request.POST.get('delete_image_id')
            MovieImage.objects.filter(id=image_id, movie=movie).delete()
            messages.success(request, 'Gallery image deleted.')

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






