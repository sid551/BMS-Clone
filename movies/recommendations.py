"""
Recommendation logic for movies.
All functions return querysets or lists and handle empty states gracefully.
Uses Django cache for high performance.
"""
from django.db.models import Count, Q
from django.core.cache import cache
from .models import Movie


def get_similar_movies(movie, limit=6):
    """
    Movies sharing the same genres and/or languages as the given movie.
    Ranked by number of matching genres (descending), then release date (descending).
    Excludes the current movie.
    """
    genre_ids = list(movie.genres.values_list('id', flat=True))
    language_ids = list(movie.languages.values_list('id', flat=True))

    if not genre_ids and not language_ids:
        return Movie.objects.none()

    cache_key = f'rec_similar_{movie.pk}_{limit}'
    movie_ids = cache.get(cache_key)

    if movie_ids is None:
        qs = (
            Movie.objects
            .exclude(pk=movie.pk)
            .filter(
                Q(genres__in=genre_ids) | Q(languages__in=language_ids)
            )
            .annotate(match_count=Count('genres', filter=Q(genres__in=genre_ids)))
            .order_by('-match_count', '-release_date')
            .distinct()[:limit]
        )
        movie_ids = list(qs.values_list('id', flat=True))
        cache.set(cache_key, movie_ids, 180)

    return Movie.objects.filter(pk__in=movie_ids).prefetch_related('genres', 'languages')


def get_trending_movies(exclude_ids=None, limit=10):
    """
    Top-rated movies ordered by rating desc, then by total confirmed bookings desc.
    Optionally exclude a set/list of movie IDs.
    """
    exclude_ids = exclude_ids or set()
    qs = (
        Movie.objects
        .exclude(pk__in=exclude_ids)
        .annotate(
            booking_count=Count(
                'schedules__bookings',
                filter=Q(schedules__bookings__status__in=['confirmed', 'completed'])
            )
        )
        .order_by('-rating', '-booking_count', '-release_date')
        .prefetch_related('genres', 'languages')
        .distinct()[:limit]
    )
    return list(qs)


def get_recently_released(exclude_ids=None, limit=10):
    """
    Latest released movies ordered by release_date descending.
    Excludes movies with no release date.
    Optionally exclude a list of movie IDs.
    """
    cache_key = f'rec_recent_{limit}'
    movie_ids = cache.get(cache_key)

    if movie_ids is None:
        qs = Movie.objects.filter(release_date__isnull=False).order_by('-release_date')[:limit]
        movie_ids = list(qs.values_list('id', flat=True))
        cache.set(cache_key, movie_ids, 180)

    qs_result = Movie.objects.filter(pk__in=movie_ids).prefetch_related('genres')
    if exclude_ids:
        qs_result = qs_result.exclude(pk__in=exclude_ids)
    return qs_result[:limit]


def get_personalized_recommendations(user, request=None, limit=6):
    """
    Generate rule-based movie recommendations for an authenticated user.
    Analyzes user's booking history and session-tracked recently viewed movies.
    Recommends unbooked movies matching preferred genres or languages.
    Falls back to trending/popular movies if user history is sparse.
    Excludes movies the user has already booked.
    """
    from .models import Booking, Genre, Language

    # 1. Fetch user's booked movie IDs if authenticated
    booked_movie_ids = []
    if user and user.is_authenticated:
        booked_movie_ids = list(
            Booking.objects
            .filter(user=user, status__in=['confirmed', 'completed', 'pending'])
            .values_list('movie_id', flat=True)
            .distinct()
        )

    # 2. Fetch session recently viewed movie IDs
    recently_viewed_ids = []
    if request and hasattr(request, 'session'):
        recently_viewed_ids = request.session.get('recently_viewed_movies', [])

    # Exclude movies already booked by user
    exclude_set = set(m_id for m_id in booked_movie_ids if m_id is not None)

    # History movie IDs = booked movies + recently viewed movies
    history_movie_ids = list(set([m_id for m_id in (booked_movie_ids + recently_viewed_ids) if m_id is not None]))

    recommendations = []

    if history_movie_ids:
        # Extract preferred genre & language names from history movies
        pref_genres = list(
            Genre.objects
            .filter(movie_id__in=history_movie_ids)
            .values_list('name', flat=True)
            .distinct()
        )
        pref_langs = list(
            Language.objects
            .filter(movie_id__in=history_movie_ids)
            .values_list('name', flat=True)
            .distinct()
        )

        if pref_genres or pref_langs:
            candidate_qs = Movie.objects.exclude(pk__in=exclude_set)

            filter_conditions = Q()
            if pref_genres:
                filter_conditions |= Q(genres__name__in=pref_genres)
            if pref_langs:
                filter_conditions |= Q(languages__name__in=pref_langs)

            candidate_qs = (
                candidate_qs
                .filter(filter_conditions)
                .annotate(
                    genre_matches=Count('genres', filter=Q(genres__name__in=pref_genres)) if pref_genres else Count('id')
                )
                .order_by('-genre_matches', '-rating', '-release_date')
                .prefetch_related('genres', 'languages')
                .distinct()[:limit]
            )
            recommendations = list(candidate_qs)

    # 3. Fallback / Top up with trending movies if insufficient candidates
    if len(recommendations) < limit:
        needed = limit - len(recommendations)
        already_recommended_ids = exclude_set.union({m.pk for m in recommendations})
        trending_fallback = list(get_trending_movies(exclude_ids=already_recommended_ids, limit=needed))
        recommendations.extend(trending_fallback)

    return recommendations[:limit]

