from django.contrib import admin
from django.utils import timezone
from .models import (
    Genre, Language, CastMember,
    Movie, MovieImage,
    Theater, ShowSchedule,
    Seat, Booking, Review, ReportedReview,
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
    fields = ['theater', 'show_time', 'price', 'available_seats']


class SeatInline(admin.TabularInline):
    model = Seat
    extra = 0
    fields = ['seat_number', 'is_booked']


# ---------------------------------------------------------------------------
# Lookup models
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
    list_display = ['name', 'role', 'photo']
    list_filter = ['role']
    search_fields = ['name']


# ---------------------------------------------------------------------------
# Movie
# ---------------------------------------------------------------------------

@admin.register(Movie)
class MovieAdmin(admin.ModelAdmin):
    list_display = ['title', 'status', 'release_date', 'duration_minutes', 'age_certification', 'rating']
    list_filter = ['status', 'genres', 'languages']
    search_fields = ['title', 'description']
    filter_horizontal = ['genres', 'languages', 'cast']
    inlines = [MovieImageInline, ShowScheduleInline]
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


@admin.register(MovieImage)
class MovieImageAdmin(admin.ModelAdmin):
    list_display = ['movie', 'caption']
    search_fields = ['movie__title', 'caption']


# ---------------------------------------------------------------------------
# Theater & ShowSchedule
# ---------------------------------------------------------------------------

@admin.register(Theater)
class TheaterAdmin(admin.ModelAdmin):
    list_display = ['name', 'location', 'total_seats']
    search_fields = ['name', 'location']
    inlines = [SeatInline]


@admin.register(ShowSchedule)
class ShowScheduleAdmin(admin.ModelAdmin):
    list_display = ['movie', 'theater', 'show_time', 'price', 'available_seats']
    list_filter = ['theater', 'movie']
    search_fields = ['movie__title', 'theater__name']


# ---------------------------------------------------------------------------
# Seat & Booking
# ---------------------------------------------------------------------------

@admin.register(Seat)
class SeatAdmin(admin.ModelAdmin):
    list_display = ['seat_number', 'theater', 'is_booked']
    list_filter = ['is_booked', 'theater']


# ---------------------------------------------------------------------------
# Booking
# ---------------------------------------------------------------------------

@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = [
        'booking_reference', 'user', 'show_schedule', 'number_of_seats',
        'total_price', 'status', 'booked_at'
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
    date_hierarchy = 'booked_at'
    
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
    
    actions = ['confirm_selected_bookings', 'cancel_selected_bookings']
    
    def confirm_selected_bookings(self, request, queryset):
        """Bulk confirm bookings"""
        confirmed = 0
        failed = 0
        
        for booking in queryset.filter(status='pending'):
            try:
                booking.confirm_booking()
                confirmed += 1
            except Exception as e:
                failed += 1
                self.message_user(request, f'Failed to confirm {booking.booking_reference}: {str(e)}', level='ERROR')
        
        if confirmed:
            self.message_user(request, f'Successfully confirmed {confirmed} booking(s).')
        if failed:
            self.message_user(request, f'{failed} booking(s) failed.', level='WARNING')
    
    confirm_selected_bookings.short_description = 'Confirm selected bookings'
    
    def cancel_selected_bookings(self, request, queryset):
        """Bulk cancel bookings"""
        cancelled = 0
        failed = 0
        
        for booking in queryset.exclude(status__in=['cancelled', 'completed']):
            try:
                booking.cancel_booking()
                cancelled += 1
            except Exception as e:
                failed += 1
                self.message_user(request, f'Failed to cancel {booking.booking_reference}: {str(e)}', level='ERROR')
        
        if cancelled:
            self.message_user(request, f'Successfully cancelled {cancelled} booking(s).')
        if failed:
            self.message_user(request, f'{failed} booking(s) failed.', level='WARNING')
    
    cancel_selected_bookings.short_description = 'Cancel selected bookings'


# ---------------------------------------------------------------------------
# Review
# ---------------------------------------------------------------------------

class ReviewInline(admin.TabularInline):
    model = Review
    extra = 0
    readonly_fields = ['user', 'rating', 'title', 'is_verified', 'created_at']
    fields = ['user', 'rating', 'title', 'is_verified', 'is_active', 'created_at']
    can_delete = True


# Add review inline to MovieAdmin
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


# ---------------------------------------------------------------------------
# Reported Reviews
# ---------------------------------------------------------------------------

class ReportsInline(admin.TabularInline):
    model = ReportedReview
    extra = 0
    readonly_fields = ['reported_by', 'reason', 'comments', 'status', 'reported_at']
    fields = ['reported_by', 'reason', 'comments', 'status', 'reported_at']
    can_delete = False
    show_change_link = True


# Attach reports inline to ReviewAdmin
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

    # ---- Status actions ----

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

    # ---- Review visibility actions ----

    def hide_reported_review(self, request, queryset):
        """Hide the reviews linked to selected reports and update avg rating."""
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
        """Restore hidden reviews and dismiss the reports."""
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
