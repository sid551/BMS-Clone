from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone
from .models import Movie, Theater, Seat, Booking, ShowSchedule, Review, ReportedReview
from .forms import ReviewForm, ReportReviewForm


def movie_list(request):
    search_query = request.GET.get('search')
    if search_query:
        movies = Movie.objects.filter(title__icontains=search_query)
    else:
        movies = Movie.objects.all()
    return render(request, 'movies/movie_list.html', {'movies': movies})


def theater_list(request, movie_id):
    movie = get_object_or_404(Movie, id=movie_id)
    # Support both legacy Theater FK and new ShowSchedule
    schedules = ShowSchedule.objects.filter(movie=movie).select_related('theater')
    theaters = Theater.objects.filter(movie=movie)
    return render(request, 'movies/theater_list.html', {
        'movie': movie,
        'theaters': theaters,
        'schedules': schedules,
    })


@login_required(login_url='/login/')
def book_seats(request, theater_id):
    theater = get_object_or_404(Theater, id=theater_id)
    seats = Seat.objects.filter(theater=theater)

    # Try to find a ShowSchedule for this theater
    schedule = ShowSchedule.objects.filter(theater=theater).first()

    if request.method == 'POST':
        selected_seats = request.POST.getlist('seats')

        if not selected_seats:
            return render(request, 'movies/seat_selection.html', {
                'theaters': theater,
                'seats': seats,
                'error': 'No seat selected.',
            })

        # --- New booking flow: use ShowSchedule ---
        if schedule:
            number_of_seats = len(selected_seats)

            # Check available seats BEFORE creating the booking
            if number_of_seats > schedule.available_seats:
                return render(request, 'movies/seat_selection.html', {
                    'theaters': theater,
                    'seats': seats,
                    'schedule': schedule,
                    'error': f'Not enough seats available. Only {schedule.available_seats} seat{"s" if schedule.available_seats != 1 else ""} left.',
                })
            booking = Booking(
                user=request.user,
                show_schedule=schedule,
                number_of_seats=number_of_seats,
                status='pending',
            )
            try:
                booking.save()
                booking.confirm_booking()
                # Mark individual seats as booked
                Seat.objects.filter(id__in=selected_seats).update(is_booked=True)
                messages.success(request, f'Booking confirmed! Reference: {booking.booking_reference}')
                return redirect('profile')
            except ValidationError as e:
                return render(request, 'movies/seat_selection.html', {
                    'theaters': theater,
                    'seats': seats,
                    'error': str(e.message if hasattr(e, 'message') else e),
                })

        # --- Legacy booking flow: no ShowSchedule ---
        error_seats = []
        for seat_id in selected_seats:
            seat = get_object_or_404(Seat, id=seat_id, theater=theater)
            if seat.is_booked:
                error_seats.append(seat.seat_number)
                continue
            Booking.objects.create(
                user=request.user,
                seat=seat,
                movie=theater.movie,
                theater=theater,
                number_of_seats=1,
                show_schedule=None,
            )
            seat.is_booked = True
            seat.save()

        if error_seats:
            return render(request, 'movies/seat_selection.html', {
                'theaters': theater,
                'seats': seats,
                'error': f'Already booked: {", ".join(error_seats)}',
            })

        return redirect('profile')

    return render(request, 'movies/seat_selection.html', {
        'theaters': theater,
        'seats': seats,
        'schedule': schedule,
    })


@login_required(login_url='/login/')
def add_review(request, movie_id):
    movie = get_object_or_404(Movie, id=movie_id)

    # Prevent duplicate
    existing = Review.objects.filter(user=request.user, movie=movie).first()
    if existing:
        messages.info(request, 'You have already reviewed this movie. Edit your review instead.')
        return redirect('edit_review', review_id=existing.id)

    if request.method == 'POST':
        form = ReviewForm(request.POST)
        if form.is_valid():
            review = form.save(commit=False)
            review.user = request.user
            review.movie = movie

            # Check 1: confirmed booking exists
            user_bookings = Booking.objects.filter(
                user=request.user,
                status__in=['confirmed', 'completed'],
            ).filter(
                Q(show_schedule__movie=movie) | Q(movie=movie)
            )

            if not user_bookings.exists():
                messages.error(request, 'You can only review a movie after a confirmed booking.')
            else:
                # Check 2: show must have ended — only trust ShowSchedule.show_time
                show_ended = user_bookings.filter(
                    show_schedule__show_time__lt=timezone.now()
                ).exists()

                if not show_ended:
                    messages.error(request, 'You can only review a movie after the show has ended.')
                else:
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
