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
    Ranked by number of matching genres (descending), then rating, then release date.
    If fewer matching movies exist, tops up with other top-rated movies.
    Excludes the current movie.
    """
    if not movie:
        return []

    genre_ids = list(movie.genres.values_list('id', flat=True))
    language_ids = list(movie.languages.values_list('id', flat=True))

    qs = Movie.objects.exclude(pk=movie.pk)

    similar_list = []
    if genre_ids or language_ids:
        matching_qs = (
            qs.filter(Q(genres__in=genre_ids) | Q(languages__in=language_ids))
            .annotate(match_count=Count('genres', filter=Q(genres__in=genre_ids)))
            .order_by('-match_count', '-rating', '-release_date', '-id')
            .prefetch_related('genres', 'languages')
            .distinct()
        )
        similar_list = list(matching_qs[:limit])

    # Top up with fallback movies if matching count is below limit
    if len(similar_list) < limit:
        already_ids = {m.pk for m in similar_list} | {movie.pk}
        fallback_qs = list(
            qs.exclude(pk__in=already_ids)
            .order_by('-rating', '-release_date', '-id')
            .prefetch_related('genres', 'languages')
            .distinct()[:(limit - len(similar_list))]
        )
        similar_list.extend(fallback_qs)

    return similar_list[:limit]


def get_trending_movies(exclude_ids=None, limit=6):
    """
    Top-rated movies ordered by rating desc, booking count desc, release date desc.
    Optionally excludes a set/list of movie IDs.
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
        .order_by('-rating', '-booking_count', '-release_date', '-id')
        .prefetch_related('genres', 'languages')
        .distinct()[:limit]
    )
    return list(qs)


def get_recently_released(exclude_ids=None, limit=6):
    """
    Latest released movies ordered by release_date descending, then id descending.
    Optionally excludes a set/list of movie IDs.
    """
    exclude_ids = exclude_ids or set()
    qs = (
        Movie.objects
        .exclude(pk__in=exclude_ids)
        .order_by('-release_date', '-id')
        .prefetch_related('genres', 'languages')
        .distinct()[:limit]
    )
    return list(qs)


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
                .order_by('-genre_matches', '-rating', '-release_date', '-id')
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
