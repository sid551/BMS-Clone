from datetime import datetime, timedelta
from django.contrib import admin
from django.utils import timezone
from django.utils.html import mark_safe
from django.urls import path, reverse
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db.models import Q

from .models import (
    Genre, Language, CastMember,
    Movie, MovieImage,
    Theater, Screen, ShowSchedule,
    Seat, Booking, BookingSeat, Review, ReportedReview,
)


# ---------------------------------------------------------------------------
# Inlines
# ---------------------------------------------------------------------------

class MovieImageInline(admin.TabularInline):
    model = MovieImage
    extra = 1
    fields = ['image', 'caption']


class ShowScheduleInline(admin.TabularInline):
    model = ShowSchedule
    extra = 1
    fields = ['theater', 'screen', 'show_time', 'price', 'available_seats']


class SeatInline(admin.TabularInline):
    model = Seat
    extra = 0
    fields = ['seat_number', 'seat_type', 'price_multiplier', 'is_active', 'is_booked']


class BookingSeatInline(admin.TabularInline):
    model = BookingSeat
    extra = 0
    readonly_fields = ['show_schedule', 'seat', 'price']
    fields = ['show_schedule', 'seat', 'price']
    can_delete = True


class ScreenInline(admin.TabularInline):
    model = Screen
    extra = 1
    fields = ['name', 'screen_type', 'total_rows', 'seats_per_row']


# ---------------------------------------------------------------------------
# Lookup & Taxonomy models
# ---------------------------------------------------------------------------

@admin.register(Genre)
class GenreAdmin(admin.ModelAdmin):
    list_display = ['name']
    search_fields = ['name']


@admin.register(Language)
class LanguageAdmin(admin.ModelAdmin):
    list_display = ['name']
    search_fields = ['name']


@admin.register(CastMember)
class CastMemberAdmin(admin.ModelAdmin):
    list_display = ['photo_preview', 'name', 'role']
    list_filter = ['role']
    search_fields = ['name']

    def photo_preview(self, obj):
        if obj.photo:
            return mark_safe(f'<img src="{obj.photo.url}" style="width: 40px; height: 40px; border-radius: 50%; object-fit: cover;" />')
        return "No Photo"
    photo_preview.short_description = 'Photo'


# ---------------------------------------------------------------------------
# Movie
# ---------------------------------------------------------------------------

@admin.register(Movie)
class MovieAdmin(admin.ModelAdmin):
    list_display = ['poster_preview', 'title', 'status', 'release_date', 'duration_minutes', 'age_certification', 'rating']
    list_filter = ['status', 'genres', 'languages']
    search_fields = ['title', 'description']
    filter_horizontal = ['genres', 'languages', 'cast']
    inlines = [MovieImageInline, ShowScheduleInline]
    actions = ['recalculate_ratings']

    fieldsets = (
        ('Basic Info', {
            'fields': ('title', 'description', 'release_date', 'duration_minutes', 'age_certification', 'status')
        }),
        ('Media', {
            'fields': ('poster', 'trailer_url', 'rating')
        }),
        ('Classification', {
            'fields': ('genres', 'languages', 'cast')
        }),
    )

    def poster_preview(self, obj):
        if obj.poster:
            return mark_safe(f'<img src="{obj.poster.url}" style="width: 36px; height: 50px; border-radius: 4px; object-fit: cover;" />')
        return "No Poster"
    poster_preview.short_description = 'Poster'

    def recalculate_ratings(self, request, queryset):
        for movie in queryset:
            movie.update_rating()
        self.message_user(request, f'Updated ratings for {queryset.count()} movie(s).')
    recalculate_ratings.short_description = 'Recalculate average user rating'


@admin.register(MovieImage)
class MovieImageAdmin(admin.ModelAdmin):
    list_display = ['movie', 'caption']
    search_fields = ['movie__title', 'caption']


# ---------------------------------------------------------------------------
# Theater & Screen
# ---------------------------------------------------------------------------

@admin.register(Theater)
class TheaterAdmin(admin.ModelAdmin):
    list_display = ['name', 'location', 'total_seats']
    search_fields = ['name', 'location']
    inlines = [ScreenInline, SeatInline]


@admin.register(Screen)
class ScreenAdmin(admin.ModelAdmin):
    list_display = ['name', 'theater', 'screen_type', 'total_rows', 'seats_per_row', 'total_seats', 'seat_map_link']
    list_filter = ['screen_type', 'theater']
    search_fields = ['name', 'theater__name']
    actions = ['generate_screen_seats']

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        obj.generate_seats()
        messages.info(request, f'Seat layout grid automatically updated for screen "{obj.name}" ({obj.total_seats} total seats).')

    def seat_map_link(self, obj):
        url = f"{reverse('admin:movies_seat_matrix')}?theater_id={obj.theater.id}&screen_id={obj.id}"
        return mark_safe(f'<a href="{url}" class="button" style="background:#059669; color:white; font-size:0.75rem; padding:3px 8px;">💺 Seat Map</a>')
    seat_map_link.short_description = 'Interactive Layout'

    def generate_screen_seats(self, request, queryset):
        count = 0
        for screen in queryset:
            screen.generate_seats()
            count += 1
        self.message_user(request, f'Seat grids generated for {count} screen(s).')
    generate_screen_seats.short_description = 'Generate seat layout grid'


# ---------------------------------------------------------------------------
# ShowSchedule with Bulk Schedule Generator
# ---------------------------------------------------------------------------

@admin.register(ShowSchedule)
class ShowScheduleAdmin(admin.ModelAdmin):
    change_list_template = 'admin/movies/showschedule/change_list.html'
    list_display = ['movie', 'theater', 'screen', 'show_time', 'price', 'available_seats']
    list_filter = ['theater', 'screen', 'movie']
    search_fields = ['movie__title', 'theater__name', 'screen__name']
    actions = ['reset_available_seats', 'bulk_update_price_200']

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('bulk-add/', self.admin_site.admin_view(self.bulk_add_view), name='movies_showschedule_bulk_add'),
        ]
        return custom_urls + urls

    def bulk_add_view(self, request):
        if request.method == 'POST':
            movie_id = request.POST.get('movie_id')
            screen_id = request.POST.get('screen_id')
            start_date_str = request.POST.get('start_date')
            end_date_str = request.POST.get('end_date')
            time_slots_raw = request.POST.get('time_slots', '')
            price = request.POST.get('price', 200.00)

            movie = get_object_or_404(Movie, id=movie_id)
            screen = get_object_or_404(Screen, id=screen_id)

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

                self.message_user(request, f'Successfully generated {created_count} show schedule(s) for "{movie.title}" on {screen.name}.')
                return redirect('admin:movies_showschedule_changelist')
            except Exception as e:
                self.message_user(request, f'Error generating schedules: {str(e)}', level=messages.ERROR)

        movies = Movie.objects.all()
        screens = Screen.objects.select_related('theater').all()
        context = {
            **self.admin_site.each_context(request),
            'title': 'Bulk Add Show Schedules',
            'movies': movies,
            'screens': screens,
            'opts': self.model._meta,
        }
        return render(request, 'admin/movies/showschedule/bulk_add.html', context)

    def reset_available_seats(self, request, queryset):
        count = 0
        for sch in queryset:
            total = sch.screen.total_seats if sch.screen else 100
            booked = BookingSeat.objects.filter(show_schedule=sch).count()
            sch.available_seats = max(0, total - booked)
            sch.save(update_fields=['available_seats'])
            count += 1
        self.message_user(request, f'Reset available seats for {count} schedule(s).')
    reset_available_seats.short_description = 'Reset available seats capacity'

    def bulk_update_price_200(self, request, queryset):
        updated = queryset.update(price=200.00)
        self.message_user(request, f'Updated price to ₹200.00 for {updated} schedule(s).')
    bulk_update_price_200.short_description = 'Set price to ₹200.00'


# ---------------------------------------------------------------------------
# Seat & Interactive Seat Matrix
# ---------------------------------------------------------------------------

@admin.register(Seat)
class SeatAdmin(admin.ModelAdmin):
    change_list_template = 'admin/movies/seat/change_list.html'
    list_display = ['seat_number', 'theater', 'screen', 'seat_type', 'price_multiplier', 'is_active', 'is_booked']
    list_filter = ['seat_type', 'is_active', 'is_booked', 'theater', 'screen']
    search_fields = ['seat_number', 'theater__name', 'screen__name']
    actions = [
        'mark_as_available', 'mark_as_booked', 'mark_as_maintenance',
        'set_tier_regular', 'set_tier_premium', 'set_tier_recliner'
    ]

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('matrix/', self.admin_site.admin_view(self.matrix_view), name='movies_seat_matrix'),
        ]
        return custom_urls + urls

    def matrix_view(self, request):
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

        context = {
            **self.admin_site.each_context(request),
            'title': 'Interactive Seat Matrix',
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
            'opts': self.model._meta,
        }
        return render(request, 'admin/movies/seat/seat_matrix.html', context)

    # Bulk seat actions
    def mark_as_available(self, request, queryset):
        updated = queryset.update(is_active=True, is_booked=False)
        self.message_user(request, f'{updated} seat(s) marked as Available.')
    mark_as_available.short_description = 'Mark selected seats as Available'

    def mark_as_booked(self, request, queryset):
        updated = queryset.update(is_active=True, is_booked=True)
        self.message_user(request, f'{updated} seat(s) marked as Booked.')
    mark_as_booked.short_description = 'Mark selected seats as Booked'

    def mark_as_maintenance(self, request, queryset):
        updated = queryset.update(is_active=False)
        self.message_user(request, f'{updated} seat(s) marked as Out of Service (Maintenance).')
    mark_as_maintenance.short_description = 'Mark selected seats as Out of Service (Maintenance)'

    def set_tier_regular(self, request, queryset):
        updated = queryset.update(seat_type='regular', price_multiplier=1.00)
        self.message_user(request, f'{updated} seat(s) updated to Regular Tier (1.0x).')
    set_tier_regular.short_description = 'Set Tier to Regular (1.0x)'

    def set_tier_premium(self, request, queryset):
        updated = queryset.update(seat_type='premium', price_multiplier=1.20)
        self.message_user(request, f'{updated} seat(s) updated to Premium Tier (1.2x).')
    set_tier_premium.short_description = 'Set Tier to Premium (1.2x)'

    def set_tier_recliner(self, request, queryset):
        updated = queryset.update(seat_type='recliner', price_multiplier=1.50)
        self.message_user(request, f'{updated} seat(s) updated to Recliner Tier (1.5x).')
    set_tier_recliner.short_description = 'Set Tier to Recliner (1.5x)'


@admin.register(BookingSeat)
class BookingSeatAdmin(admin.ModelAdmin):
    list_display = ['booking', 'show_schedule', 'seat', 'price']
    list_filter = ['show_schedule__movie', 'show_schedule__theater']
    search_fields = ['booking__booking_reference', 'seat__seat_number']


# ---------------------------------------------------------------------------
# Booking
# ---------------------------------------------------------------------------

@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = [
        'booking_reference', 'user', 'show_schedule', 'get_booked_seats',
        'number_of_seats', 'total_price', 'status_badge', 'booked_at'
    ]
    list_filter = ['status', 'booked_at', 'show_schedule__movie', 'show_schedule__theater']
    search_fields = [
        'booking_reference', 'user__username', 'user__email',
        'show_schedule__movie__title', 'show_schedule__theater__name'
    ]
    readonly_fields = [
        'booking_reference', 'total_price', 'booked_at', 'updated_at',
        'movie', 'theater'
    ]
    inlines = [BookingSeatInline]
    date_hierarchy = 'booked_at'
    actions = ['confirm_selected_bookings', 'cancel_selected_bookings']

    fieldsets = (
        ('Booking Info', {
            'fields': ('booking_reference', 'user', 'show_schedule', 'number_of_seats', 'status')
        }),
        ('Pricing', {
            'fields': ('total_price',)
        }),
        ('Timestamps', {
            'fields': ('booked_at', 'updated_at')
        }),
        ('Legacy (Auto-populated)', {
            'fields': ('movie', 'theater', 'seat'),
            'classes': ('collapse',)
        }),
    )

    def status_badge(self, obj):
        colors = {
            'confirmed': '#059669',
            'completed': '#2563eb',
            'pending': '#d97706',
            'cancelled': '#dc2626',
        }
        color = colors.get(obj.status, '#64748b')
        return mark_safe(f'<span style="background-color: {color}; color: white; padding: 3px 8px; border-radius: 4px; font-weight: 600; font-size: 0.75rem;">{obj.get_status_display()}</span>')
    status_badge.short_description = 'Status'

    def get_booked_seats(self, obj):
        seats = [bs.seat.seat_number for bs in obj.booked_seats.all()]
        if seats:
            return ", ".join(seats)
        if obj.seat:
            return obj.seat.seat_number
        return "N/A"
    get_booked_seats.short_description = 'Seats'

    def confirm_selected_bookings(self, request, queryset):
        confirmed = 0
        failed = 0
        for booking in queryset.filter(status='pending'):
            try:
                booking.confirm_booking()
                confirmed += 1
            except Exception as e:
                failed += 1
                self.message_user(request, f'Failed to confirm {booking.booking_reference}: {str(e)}', level=messages.ERROR)

        if confirmed:
            self.message_user(request, f'Successfully confirmed {confirmed} booking(s).')
        if failed:
            self.message_user(request, f'{failed} booking(s) failed.', level=messages.WARNING)
    confirm_selected_bookings.short_description = 'Confirm selected bookings'

    def cancel_selected_bookings(self, request, queryset):
        cancelled = 0
        failed = 0
        for booking in queryset.exclude(status__in=['cancelled', 'completed']):
            try:
                booking.cancel_booking()
                cancelled += 1
            except Exception as e:
                failed += 1
                self.message_user(request, f'Failed to cancel {booking.booking_reference}: {str(e)}', level=messages.ERROR)

        if cancelled:
            self.message_user(request, f'Successfully cancelled {cancelled} booking(s).')
        if failed:
            self.message_user(request, f'{failed} booking(s) failed.', level=messages.WARNING)
    cancel_selected_bookings.short_description = 'Cancel selected bookings'


# ---------------------------------------------------------------------------
# Review & Reports Inline
# ---------------------------------------------------------------------------

class ReviewInline(admin.TabularInline):
    model = Review
    extra = 0
    readonly_fields = ['user', 'rating', 'title', 'is_verified', 'created_at']
    fields = ['user', 'rating', 'title', 'is_verified', 'is_active', 'created_at']
    can_delete = True


# Attach inline
MovieAdmin.inlines = [MovieImageInline, ShowScheduleInline, ReviewInline]


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ['movie', 'user', 'rating', 'title', 'is_verified', 'is_active', 'report_count', 'created_at']
    list_filter = ['rating', 'is_active', 'is_verified', 'created_at']
    search_fields = ['user__username', 'movie__title', 'title', 'text']
    readonly_fields = ['is_verified', 'created_at', 'updated_at', 'report_count']
    date_hierarchy = 'created_at'
    actions = ['activate_reviews', 'deactivate_reviews']

    fieldsets = (
        ('Review', {
            'fields': ('movie', 'user', 'rating', 'title', 'text')
        }),
        ('Status', {
            'fields': ('is_active', 'is_verified', 'report_count')
        }),
        ('Moderation', {
            'fields': ('moderation_note',),
            'description': 'Internal notes — never shown to users.',
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at')
        }),
    )

    def report_count(self, obj):
        return obj.reports.count()
    report_count.short_description = 'Reports'

    def activate_reviews(self, request, queryset):
        updated = queryset.update(is_active=True)
        for movie in Movie.objects.filter(reviews__in=queryset).distinct():
            movie.update_rating()
        self.message_user(request, f'{updated} review(s) activated.')
    activate_reviews.short_description = 'Activate selected reviews'

    def deactivate_reviews(self, request, queryset):
        updated = queryset.update(is_active=False)
        for movie in Movie.objects.filter(reviews__in=queryset).distinct():
            movie.update_rating()
        self.message_user(request, f'{updated} review(s) deactivated.')
    deactivate_reviews.short_description = 'Deactivate selected reviews'


class ReportsInline(admin.TabularInline):
    model = ReportedReview
    extra = 0
    readonly_fields = ['reported_by', 'reason', 'comments', 'status', 'reported_at']
    fields = ['reported_by', 'reason', 'comments', 'status', 'reported_at']
    can_delete = False
    show_change_link = True


ReviewAdmin.inlines = [ReportsInline]


@admin.register(ReportedReview)
class ReportedReviewAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'review_summary', 'reported_by', 'reason', 'status',
        'reported_at', 'reviewed_by'
    ]
    list_filter = ['status', 'reason', 'reported_at']
    search_fields = [
        'reported_by__username',
        'review__user__username',
        'review__movie__title',
        'review__title',
        'comments',
    ]
    readonly_fields = ['reported_by', 'review', 'reported_at', 'review_detail']
    date_hierarchy = 'reported_at'
    actions = [
        'mark_reviewed', 'mark_dismissed', 'mark_resolved',
        'hide_reported_review', 'restore_reported_review',
    ]

    fieldsets = (
        ('Report', {
            'fields': ('review', 'review_detail', 'reported_by', 'reason', 'comments', 'reported_at')
        }),
        ('Moderation', {
            'fields': ('status', 'reviewed_by', 'reviewed_at')
        }),
    )

    def review_summary(self, obj):
        return f'"{obj.review.title}" by {obj.review.user.username} on {obj.review.movie.title}'
    review_summary.short_description = 'Review'

    def review_detail(self, obj):
        return obj.review.text
    review_detail.short_description = 'Review Text'

    def _set_status(self, request, queryset, status):
        queryset.update(
            status=status,
            reviewed_by=request.user,
            reviewed_at=timezone.now()
        )
        self.message_user(request, f'{queryset.count()} report(s) marked as {status}.')

    def mark_reviewed(self, request, queryset):
        self._set_status(request, queryset, 'reviewed')
    mark_reviewed.short_description = 'Mark as Reviewed'

    def mark_dismissed(self, request, queryset):
        self._set_status(request, queryset, 'dismissed')
    mark_dismissed.short_description = 'Dismiss selected reports'

    def mark_resolved(self, request, queryset):
        self._set_status(request, queryset, 'resolved')
    mark_resolved.short_description = 'Mark as Resolved'

    def hide_reported_review(self, request, queryset):
        review_ids = queryset.values_list('review_id', flat=True).distinct()
        reviews = Review.objects.filter(pk__in=review_ids)
        reviews.update(is_active=False)
        for movie in Movie.objects.filter(reviews__in=reviews).distinct():
            movie.update_rating()
        queryset.update(
            status='resolved',
            reviewed_by=request.user,
            reviewed_at=timezone.now()
        )
        self.message_user(request, f'{reviews.count()} review(s) hidden and reports resolved.')
    hide_reported_review.short_description = 'Hide review(s) and resolve reports'

    def restore_reported_review(self, request, queryset):
        review_ids = queryset.values_list('review_id', flat=True).distinct()
        reviews = Review.objects.filter(pk__in=review_ids)
        reviews.update(is_active=True)
        for movie in Movie.objects.filter(reviews__in=reviews).distinct():
            movie.update_rating()
        queryset.update(
            status='dismissed',
            reviewed_by=request.user,
            reviewed_at=timezone.now()
        )
        self.message_user(request, f'{reviews.count()} review(s) restored and reports dismissed.')
    restore_reported_review.short_description = 'Restore review(s) and dismiss reports'
