"""
Recommendation logic for movies.
All functions return querysets or lists and handle empty states gracefully.
"""
from django.db.models import Count, Q
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

    return (
        Movie.objects
        .exclude(pk=movie.pk)
        .filter(
            Q(genres__in=genre_ids) | Q(languages__in=language_ids)
        )
        .annotate(match_count=Count('genres', filter=Q(genres__in=genre_ids)))
        .order_by('-match_count', '-release_date')
        .distinct()
        .prefetch_related('genres')[:limit]
    )


def get_trending_movies(exclude_ids=None, limit=10):
    """
    Top-rated movies ordered by rating desc, then by total confirmed bookings desc.
    Optionally exclude a list of movie IDs.
    """
    qs = Movie.objects.prefetch_related('genres')

    if exclude_ids:
        qs = qs.exclude(pk__in=exclude_ids)

    return (
        qs
        .annotate(
            booking_count=Count(
                'booking',
                filter=Q(booking__status__in=['confirmed', 'completed'])
            )
        )
        .order_by('-rating', '-booking_count', '-release_date')
        .distinct()[:limit]
    )


def get_recently_released(exclude_ids=None, limit=10):
    """
    Latest released movies ordered by release_date descending.
    Excludes movies with no release date.
    Optionally exclude a list of movie IDs.
    """
    qs = Movie.objects.filter(release_date__isnull=False).prefetch_related('genres')

    if exclude_ids:
        qs = qs.exclude(pk__in=exclude_ids)

    return qs.order_by('-release_date')[:limit]
