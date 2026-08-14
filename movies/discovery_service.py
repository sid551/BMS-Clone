"""
Movie Discovery Service Module.
Encapsulates query filtering, sorting, pagination, recommendation retrieval,
and context assembly for the Movie Discovery module.
"""

from django.core.paginator import Paginator
from django.db.models import Q, Count, Min, F
from django.utils import timezone
from .models import Movie, Genre, Language, Theater
from .recommendations import get_personalized_recommendations


class MovieDiscoveryService:
    @staticmethod
    def get_discovery_context(request, per_page=9):
        """
        Build and assemble complete context dictionary for the Movie Discovery listing page.
        """
        params = request.GET.dict()
        search_query = params.get('search', '').strip()
        sort_by = params.get('sort', 'title').strip()

        # Build base queryset with filters
        base_qs = MovieDiscoveryService.build_filtered_queryset(params)

        # Apply sorting
        sorted_qs = MovieDiscoveryService.apply_sorting(base_qs, sort_by)

        # Prefetch related objects to eliminate N+1 query issues
        movies_qs = sorted_qs.prefetch_related('genres', 'languages', 'cast')

        # Compute total matching count and paginate
        total_movies = movies_qs.count()
        paginator = Paginator(movies_qs, per_page)
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)

        # Retrieve filter option datasets for dropdowns
        all_genres = Genre.objects.values_list('name', flat=True).distinct().order_by('name')
        all_languages = Language.objects.values_list('name', flat=True).distinct().order_by('name')
        all_cities = Theater.objects.exclude(location='').values_list('location', flat=True).distinct().order_by('location')
        all_theaters = Theater.objects.all().order_by('name')

        time_slots = [
            {'id': 'morning', 'name': 'Morning (6 AM - 12 PM)'},
            {'id': 'afternoon', 'name': 'Afternoon (12 PM - 4 PM)'},
            {'id': 'evening', 'name': 'Evening (4 PM - 8 PM)'},
            {'id': 'night', 'name': 'Night (8 PM - 12 AM)'},
        ]

        sort_options = [
            {'id': 'title', 'name': 'Title (A-Z)'},
            {'id': 'popularity', 'name': 'Popularity (Most Booked)'},
            {'id': 'newest', 'name': 'Newest Release'},
            {'id': 'rating', 'name': 'Highest Rating'},
            {'id': 'price_low', 'name': 'Price: Low to High'},
        ]

        # Calculate count of active filters
        active_filters_count = sum(
            1 for k, v in params.items()
            if k not in ['page', 'sort'] and v.strip() != ''
        )

        # Retrieve personalized recommendations for authenticated users
        recommended_movies = []
        if request.user.is_authenticated:
            recommended_movies = get_personalized_recommendations(request.user, request=request, limit=6)

        return {
            'movies': page_obj.object_list,
            'page_obj': page_obj,
            'total_movies': total_movies,
            'search_query': search_query,
            'selected_genre': params.get('genre', '').strip(),
            'selected_language': params.get('language', '').strip(),
            'selected_city': params.get('city', '').strip(),
            'selected_theater': params.get('theater', '').strip(),
            'selected_status': params.get('status', '').strip(),
            'selected_min_rating': params.get('min_rating', '').strip(),
            'selected_release_date': params.get('release_date', '').strip(),
            'selected_show_date': params.get('show_date', '').strip(),
            'selected_time_slot': params.get('time_slot', '').strip(),
            'sort_by': sort_by,
            'all_genres': all_genres,
            'all_languages': all_languages,
            'all_cities': all_cities,
            'all_theaters': all_theaters,
            'time_slots': time_slots,
            'sort_options': sort_options,
            'active_filters_count': active_filters_count,
            'recommended_movies': recommended_movies,
        }

    @staticmethod
    def build_filtered_queryset(params):
        """Build filtered movie queryset using optimized Django ORM queries."""
        qs = Movie.objects.all()

        search_query = params.get('search', '').strip()
        if search_query:
            qs = qs.filter(title__icontains=search_query)

        genre = params.get('genre', '').strip()
        if genre:
            if genre.isdigit():
                qs = qs.filter(genres__id=genre)
            else:
                qs = qs.filter(genres__name__iexact=genre)

        language = params.get('language', '').strip()
        if language:
            if language.isdigit():
                qs = qs.filter(languages__id=language)
            else:
                qs = qs.filter(languages__name__iexact=language)

        city = params.get('city', '').strip()
        if city:
            qs = qs.filter(schedules__theater__location__icontains=city)

        theater = params.get('theater', '').strip()
        if theater:
            if theater.isdigit():
                qs = qs.filter(schedules__theater_id=theater)
            else:
                qs = qs.filter(schedules__theater__name__icontains=theater)

        status = params.get('status', '').strip()
        if status in ['now_showing', 'upcoming', 'ended']:
            qs = qs.filter(status=status)

        min_rating = params.get('min_rating', '').strip()
        if min_rating:
            try:
                rating_val = float(min_rating)
                qs = qs.filter(rating__gte=rating_val)
            except ValueError:
                pass

        release_date = params.get('release_date', '').strip()
        if release_date:
            qs = qs.filter(release_date=release_date)

        show_date = params.get('show_date', '').strip()
        if show_date:
            qs = qs.filter(schedules__show_time__date=show_date)

        time_slot = params.get('time_slot', '').strip()
        if time_slot == 'morning':
            qs = qs.filter(schedules__show_time__time__range=('06:00', '12:00'))
        elif time_slot == 'afternoon':
            qs = qs.filter(schedules__show_time__time__range=('12:00', '16:00'))
        elif time_slot == 'evening':
            qs = qs.filter(schedules__show_time__time__range=('16:00', '20:00'))
        elif time_slot == 'night':
            qs = qs.filter(schedules__show_time__time__range=('20:00', '23:59'))

        return qs.distinct()

    @staticmethod
    def apply_sorting(queryset, sort_by):
        """Apply requested sorting criteria to the movie queryset."""
        now = timezone.now()
        if sort_by == 'popularity':
            return queryset.annotate(
                popularity=Count(
                    'schedules__bookings',
                    filter=Q(schedules__bookings__status__in=['confirmed', 'completed']),
                    distinct=True
                )
            ).order_by('-popularity', '-rating', 'title')
        elif sort_by == 'newest':
            return queryset.order_by('-release_date', 'title')
        elif sort_by == 'rating':
            return queryset.order_by('-rating', '-review_count', 'title')
        elif sort_by == 'price_low':
            return queryset.annotate(
                min_price=Min('schedules__price', filter=Q(schedules__show_time__gte=now))
            ).order_by(F('min_price').asc(nulls_last=True), 'title')
        else:  # 'title' / default
            return queryset.order_by('title')
