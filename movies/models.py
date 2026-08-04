from django.db import models
from django.contrib.auth.models import User


# ---------------------------------------------------------------------------
# Lookup / reference tables
# ---------------------------------------------------------------------------

class Genre(models.Model):
    name = models.CharField(max_length=100, unique=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['name']


class Language(models.Model):
    name = models.CharField(max_length=100, unique=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['name']


class CastMember(models.Model):
    ROLE_CHOICES = [
        ('actor', 'Actor'),
        ('director', 'Director'),
        ('producer', 'Producer'),
        ('writer', 'Writer'),
    ]
    name = models.CharField(max_length=255)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='actor')
    photo = models.ImageField(upload_to='cast/', blank=True, null=True)

    def __str__(self):
        return f'{self.name} ({self.get_role_display()})'

    class Meta:
        ordering = ['name']


# ---------------------------------------------------------------------------
# Core Movie model
# ---------------------------------------------------------------------------

class Movie(models.Model):
    STATUS_CHOICES = [
        ('upcoming', 'Upcoming'),
        ('now_showing', 'Now Showing'),
        ('ended', 'Ended'),
    ]

    AGE_CERTIFICATION_CHOICES = [
        ('U', 'U - Unrestricted Public Exhibition'),
        ('U/A', 'U/A - Parental Guidance Suggested'),
        ('U/A 13+', 'U/A 13+ - Suitable for 13 years and above'),
        ('U/A 16+', 'U/A 16+ - Suitable for 16 years and above'),
        ('A', 'A - Restricted to Adults'),
        ('S', 'S - Restricted to Specialized Audiences'),
    ]

    # Basic info
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    duration_minutes = models.PositiveIntegerField(default=0, help_text='Duration in minutes')
    release_date = models.DateField(null=True, blank=True)
    age_certification = models.CharField(
        max_length=20,
        choices=AGE_CERTIFICATION_CHOICES,
        help_text='e.g. U, U/A, U/A 13+, A',
        blank=True
    )
    trailer_url = models.URLField(
        blank=True,
        null=True,
        help_text='YouTube trailer URL'
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='upcoming')

    # Primary poster
    poster = models.ImageField(upload_to='movies/posters/', blank=True, null=True)

    # Relationships
    genres = models.ManyToManyField(Genre, blank=True, related_name='movies')
    languages = models.ManyToManyField(Language, blank=True, related_name='movies')
    cast = models.ManyToManyField(CastMember, blank=True, related_name='movies')

    # Legacy field kept for backward compatibility with existing views/templates
    name = models.CharField(max_length=255, blank=True, editable=False)
    image = models.ImageField(upload_to='movies/', blank=True, null=True)
    rating = models.DecimalField(max_digits=3, decimal_places=1, default=0.0)
    review_count = models.PositiveIntegerField(default=0, editable=False)

    @property
    def duration_formatted(self):
        if not self.duration_minutes:
            return ""
        hours = self.duration_minutes // 60
        mins = self.duration_minutes % 60
        if hours > 0 and mins > 0:
            return f"{hours}h {mins}m"
        elif hours > 0:
            return f"{hours}h"
        return f"{mins}m"

    def update_rating(self):
        """Recalculate avg rating and review count from active reviews."""
        from django.db.models import Avg
        result = self.reviews.filter(is_active=True).aggregate(avg=Avg('rating'))
        self.rating = round(result['avg'] or 0, 1)
        self.review_count = self.reviews.filter(is_active=True).count()
        self.save(update_fields=['rating', 'review_count'])

    def save(self, *args, **kwargs):
        # Keep legacy `name` in sync with `title`
        self.name = self.title
        # Keep legacy `image` in sync with `poster`
        if self.poster and not self.image:
            self.image = self.poster
        super().save(*args, **kwargs)

    def __str__(self):
        return self.title

    class Meta:
        ordering = ['-release_date']


# ---------------------------------------------------------------------------
# Movie gallery images
# ---------------------------------------------------------------------------

class MovieImage(models.Model):
    movie = models.ForeignKey(Movie, on_delete=models.CASCADE, related_name='images')
    image = models.ImageField(upload_to='movies/gallery/')
    caption = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return f'Image for {self.movie.title}'


# ---------------------------------------------------------------------------
# Theater & ShowSchedule
# ---------------------------------------------------------------------------

class Theater(models.Model):
    name = models.CharField(max_length=255)
    location = models.CharField(max_length=255, blank=True)
    total_seats = models.PositiveIntegerField(default=0)

    # Legacy FK kept for existing views
    movie = models.ForeignKey(
        Movie, on_delete=models.CASCADE,
        related_name='theaters',
        null=True, blank=True
    )
    time = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'{self.name} ({self.location})' if self.location else self.name

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.pk and self.screens.exists():
            total = sum(s.total_seats for s in self.screens.all())
            if total > 0 and self.total_seats != total:
                self.total_seats = total
                super().save(update_fields=['total_seats'])

    class Meta:
        ordering = ['name']


class Screen(models.Model):
    SCREEN_TYPES = [
        ('2D', 'Standard 2D'),
        ('3D', '3D Screen'),
        ('IMAX_3D', 'IMAX 3D'),
        ('4DX', '4DX Motion'),
        ('DOLBY', 'Dolby Atmos'),
    ]

    theater = models.ForeignKey(Theater, on_delete=models.CASCADE, related_name='screens')
    name = models.CharField(max_length=100, help_text='e.g. Screen 1, Audi 2 - IMAX')
    screen_type = models.CharField(max_length=20, choices=SCREEN_TYPES, default='2D')
    total_rows = models.PositiveIntegerField(default=8, help_text='Number of rows (e.g. 8 for A-H)')
    seats_per_row = models.PositiveIntegerField(default=10, help_text='Seats per row (e.g. 10)')

    def __str__(self):
        return f'{self.theater.name} - {self.name} ({self.get_screen_type_display()})'

    @property
    def total_seats(self):
        return self.total_rows * self.seats_per_row

    def generate_seats(self, force_resync=False):
        """Auto-generate seat layout grid for this screen if not present."""
        import string
        rows = list(string.ascii_uppercase[:min(self.total_rows, 26)])

        if self.seats.exists():
            if not force_resync:
                return
            # Re-sync tiers if forced
            for row_idx, row_name in enumerate(rows):
                if row_idx < max(1, int(len(rows) * 0.40)):
                    stype, mult = 'regular', 1.00
                elif row_idx < int(len(rows) * 0.75):
                    stype, mult = 'premium', 1.20
                else:
                    stype, mult = 'recliner', 1.50
                self.seats.filter(row=row_name).update(seat_type=stype, price_multiplier=mult)
            return

        seats_to_create = []
        for row_idx, row_name in enumerate(rows):
            # Tiers: Front (Row A...) Regular (1.0x), Middle Premium (1.2x), Back Recliner (1.5x)
            if row_idx < max(1, int(len(rows) * 0.40)):
                seat_type = 'regular'
                multiplier = 1.00
            elif row_idx < int(len(rows) * 0.75):
                seat_type = 'premium'
                multiplier = 1.20
            else:
                seat_type = 'recliner'
                multiplier = 1.50

            for num in range(1, self.seats_per_row + 1):
                seat_num_str = f'{row_name}{num}'
                seats_to_create.append(Seat(
                    screen=self,
                    theater=self.theater,
                    row=row_name,
                    number=num,
                    seat_number=seat_num_str,
                    seat_type=seat_type,
                    price_multiplier=multiplier,
                    is_active=True
                ))
        Seat.objects.bulk_create(seats_to_create)

    def update_theater_capacity(self):
        """Recalculate total seats for theater from all screens."""
        if self.theater:
            total = sum(s.total_seats for s in self.theater.screens.all())
            if total > 0:
                self.theater.total_seats = total
                self.theater.save(update_fields=['total_seats'])

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.generate_seats()
        self.update_theater_capacity()

    def delete(self, *args, **kwargs):
        theater = self.theater
        super().delete(*args, **kwargs)
        if theater:
            total = sum(s.total_seats for s in theater.screens.all())
            theater.total_seats = total
            theater.save(update_fields=['total_seats'])

    class Meta:
        ordering = ['name']


class ShowSchedule(models.Model):
    movie = models.ForeignKey(Movie, on_delete=models.CASCADE, related_name='schedules')
    theater = models.ForeignKey(Theater, on_delete=models.CASCADE, related_name='schedules')
    screen = models.ForeignKey(Screen, on_delete=models.CASCADE, related_name='schedules', null=True, blank=True)
    show_time = models.DateTimeField()
    price = models.DecimalField(max_digits=8, decimal_places=2, default=0.00)
    available_seats = models.PositiveIntegerField(default=0)

    def get_max_capacity(self):
        if self.screen:
            return self.screen.total_seats
        elif self.theater:
            return self.theater.total_seats
        return 0

    def clean(self):
        super().clean()
        max_cap = self.get_max_capacity()
        if not self.available_seats or self.available_seats == 0:
            self.available_seats = max_cap if max_cap > 0 else 0
        elif max_cap > 0 and self.available_seats > max_cap:
            self.available_seats = max_cap

    def save(self, *args, **kwargs):
        # Always sync theater from screen if screen is set
        if self.screen:
            self.theater = self.screen.theater

        # Enforce max capacity cap
        max_cap = self.get_max_capacity()
        if not self.available_seats or self.available_seats == 0:
            self.available_seats = max_cap if max_cap > 0 else 0
        elif max_cap > 0 and self.available_seats > max_cap:
            self.available_seats = max_cap

        is_new = self.pk is None
        super().save(*args, **kwargs)
        if is_new and self.screen_id:
            self._generate_show_seats()

    def _generate_show_seats(self):
        seats = self.screen.seats.filter(is_active=True)
        ShowSeat.objects.bulk_create(
            [ShowSeat(show_schedule=self, seat=seat, status='available') for seat in seats],
            ignore_conflicts=True
        )

    def __str__(self):
        screen_str = f' [{self.screen.name}]' if self.screen else ''
        return f'{self.movie.title} @ {self.theater.name}{screen_str} — {self.show_time}'

    class Meta:
        ordering = ['show_time']
        unique_together = ('theater', 'show_time')


# ---------------------------------------------------------------------------
# Seat & Layout System
# ---------------------------------------------------------------------------

class Seat(models.Model):
    SEAT_TYPES = [
        ('regular', 'Executive / Regular'),
        ('premium', 'Premium'),
        ('recliner', 'Recliner / VIP'),
    ]

    theater = models.ForeignKey(Theater, on_delete=models.CASCADE, related_name='seats')
    screen = models.ForeignKey(Screen, on_delete=models.CASCADE, related_name='seats', null=True, blank=True)
    row = models.CharField(max_length=5, blank=True, default='A')
    number = models.PositiveIntegerField(default=1)
    seat_number = models.CharField(max_length=10)
    seat_type = models.CharField(max_length=20, choices=SEAT_TYPES, default='regular')
    price_multiplier = models.DecimalField(max_digits=4, decimal_places=2, default=1.00)
    is_booked = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True, help_text='Set false for broken/maintenance seats')

    def calculate_price(self, base_price):
        """Calculate seat price based on schedule base price and multiplier."""
        return round(float(base_price) * float(self.price_multiplier), 2)

    def __str__(self):
        screen_info = f' [{self.screen.name}]' if self.screen else ''
        return f'{self.seat_number} ({self.get_seat_type_display()}){screen_info} - {self.theater.name}'


class BookingSeat(models.Model):
    booking = models.ForeignKey('Booking', on_delete=models.CASCADE, related_name='booked_seats')
    show_schedule = models.ForeignKey(ShowSchedule, on_delete=models.CASCADE, related_name='booked_seats')
    seat = models.ForeignKey(Seat, on_delete=models.CASCADE, related_name='show_bookings')
    price = models.DecimalField(max_digits=8, decimal_places=2, default=0.00)

    class Meta:
        unique_together = ('show_schedule', 'seat')

    def __str__(self):
        return f'{self.seat.seat_number} for Schedule #{self.show_schedule_id}'


# ---------------------------------------------------------------------------
# Per-schedule seat status (the live seat map)
# ---------------------------------------------------------------------------

class ShowSeat(models.Model):
    STATUS_CHOICES = [
        ('available', 'Available'),
        ('reserved', 'Reserved'),
        ('booked', 'Booked'),
    ]

    RESERVATION_MINUTES = 2

    show_schedule = models.ForeignKey(
        ShowSchedule, on_delete=models.CASCADE, related_name='show_seats'
    )
    seat = models.ForeignKey(
        Seat, on_delete=models.CASCADE, related_name='show_seats'
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='available')

    # Reservation fields
    reserved_by = models.ForeignKey(
        User, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='reserved_seats'
    )
    reserved_until = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ('show_schedule', 'seat')
        ordering = ['seat__row', 'seat__number']
        indexes = [
            models.Index(fields=['show_schedule', 'status']),
            models.Index(fields=['reserved_until']),
        ]

    def __str__(self):
        return f'{self.seat.seat_number} [{self.status}] — {self.show_schedule}'

    @property
    def is_reservation_expired(self):
        """True if reserved but expiry has passed."""
        if self.status == 'reserved' and self.reserved_until:
            return timezone.now() > self.reserved_until
        return False

    @property
    def seconds_remaining(self):
        """Seconds left in reservation, 0 if expired or not reserved."""
        if self.status == 'reserved' and self.reserved_until:
            delta = self.reserved_until - timezone.now()
            return max(0, int(delta.total_seconds()))
        return 0

    def release(self):
        """Release this reservation and mark seat available."""
        self.status = 'available'
        self.reserved_by = None
        self.reserved_until = None
        self.save(update_fields=['status', 'reserved_by', 'reserved_until'])



# ---------------------------------------------------------------------------
# Booking system
# ---------------------------------------------------------------------------

import uuid
from django.core.exceptions import ValidationError
from django.db import transaction


class Booking(models.Model):
    BOOKING_STATUS = [
        ('pending', 'Pending'),
        ('confirmed', 'Confirmed'),
        ('cancelled', 'Cancelled'),
        ('completed', 'Completed'),
    ]

    # Core fields
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='bookings')
    show_schedule = models.ForeignKey(ShowSchedule, on_delete=models.CASCADE, related_name='bookings', null=True, blank=True)
    
    # Booking details
    booking_reference = models.CharField(max_length=20, unique=True, editable=False)
    number_of_seats = models.PositiveIntegerField()
    total_price = models.DecimalField(max_digits=10, decimal_places=2, editable=False, default=0)
    status = models.CharField(max_length=20, choices=BOOKING_STATUS, default='pending')
    
    # Timestamps
    booked_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Legacy fields kept for backward compatibility
    seat = models.OneToOneField(Seat, on_delete=models.CASCADE, null=True, blank=True)
    movie = models.ForeignKey(Movie, on_delete=models.CASCADE, null=True, blank=True)
    theater = models.ForeignKey(Theater, on_delete=models.CASCADE, null=True, blank=True)

    class Meta:
        ordering = ['-booked_at']
        indexes = [
            models.Index(fields=['booking_reference']),
            models.Index(fields=['user', '-booked_at']),
        ]

    def __str__(self):
        return f'{self.booking_reference} - {self.user.username} ({self.status})'

    def clean(self):
        """Validate booking before saving"""
        if self.number_of_seats <= 0:
            raise ValidationError('Number of seats must be greater than zero.')

        if self.show_schedule and self.number_of_seats > self.show_schedule.available_seats:
            raise ValidationError(
                f'Only {self.show_schedule.available_seats} seats available. Cannot book {self.number_of_seats}.'
            )

    def save(self, *args, **kwargs):
        # Generate unique booking reference
        if not self.booking_reference:
            self.booking_reference = f'BMS{uuid.uuid4().hex[:12].upper()}'

        # Calculate total price only when show_schedule exists and total_price is not set
        if self.show_schedule_id:
            if not self.total_price:
                self.total_price = self.show_schedule.price * self.number_of_seats
            self.movie = self.show_schedule.movie
            self.theater = self.show_schedule.theater
        elif not self.total_price:
            self.total_price = 0


        self.full_clean()
        super().save(*args, **kwargs)

    @transaction.atomic
    def confirm_booking(self):
        """Confirm booking and reduce available seats"""
        if self.status != 'pending':
            raise ValidationError(f'Cannot confirm booking with status: {self.status}')
        
        # Lock the show schedule row to prevent race conditions
        schedule = ShowSchedule.objects.select_for_update().get(pk=self.show_schedule.pk)
        
        if schedule.available_seats < self.number_of_seats:
            raise ValidationError(
                f'Not enough seats available. Only {schedule.available_seats} left.'
            )
        
        # Reduce available seats
        schedule.available_seats -= self.number_of_seats
        schedule.save(update_fields=['available_seats'])
        
        # Update booking status
        self.status = 'confirmed'
        self.save(update_fields=['status', 'updated_at'])

    @transaction.atomic
    def cancel_booking(self):
        """Cancel booking and restore available seats"""
        if self.status not in ['pending', 'confirmed']:
            raise ValidationError(f'Cannot cancel booking with status: {self.status}')
        
        # Only restore seats if booking was confirmed
        if self.status == 'confirmed':
            schedule = ShowSchedule.objects.select_for_update().get(pk=self.show_schedule.pk)
            schedule.available_seats += self.number_of_seats
            schedule.save(update_fields=['available_seats'])
        
        # Update booking status
        self.status = 'cancelled'
        self.save(update_fields=['status', 'updated_at'])


# ---------------------------------------------------------------------------
# Review system
# ---------------------------------------------------------------------------

from django.utils import timezone
from django.db.models import Avg


class Review(models.Model):
    RATING_CHOICES = [(i, f'{i} Star{"s" if i > 1 else ""}') for i in range(1, 6)]

    movie = models.ForeignKey(Movie, on_delete=models.CASCADE, related_name='reviews')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='reviews')

    rating = models.PositiveSmallIntegerField(choices=RATING_CHOICES)
    title = models.CharField(max_length=150)
    text = models.TextField()
    is_active = models.BooleanField(default=True)
    is_verified = models.BooleanField(
        default=False,
        help_text='Auto-set: user has a completed/confirmed booking for this movie'
    )
    moderation_note = models.TextField(
        blank=True,
        help_text='Internal note visible only to admins. Not shown to users.'
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        # One review per user per movie
        unique_together = ('movie', 'user')
        indexes = [
            models.Index(fields=['movie', 'is_active']),
        ]

    def __str__(self):
        return f'{self.user.username} → {self.movie.title} ({self.rating}★)'

    def clean(self):
        # Guard against being called before user/movie are assigned
        if not self.user_id or not self.movie_id:
            return

        # Step 1: user must have a confirmed/completed booking for this movie
        user_bookings = Booking.objects.filter(
            user=self.user,
            status__in=['confirmed', 'completed'],
        ).filter(
            models.Q(show_schedule__movie=self.movie) | models.Q(movie=self.movie)
        )

        if not user_bookings.exists():
            raise ValidationError(
                'You can only review a movie after a confirmed booking.'
            )

        # Step 2: the show the user booked must have already ended
        show_ended = user_bookings.filter(
            models.Q(show_schedule__show_time__lt=timezone.now()) |
            models.Q(show_schedule__isnull=True, theater__time__lt=timezone.now()) |
            models.Q(show_schedule__isnull=True, theater__time__isnull=True, booked_at__lt=timezone.now())
        ).exists()

        if not show_ended:
            raise ValidationError(
                'You can only review a movie after the show has ended.'
            )

    def save(self, *args, **kwargs):
        # Only run business logic validation when user and movie are set
        if self.user_id and self.movie_id:
            self.is_verified = Booking.objects.filter(
                user=self.user,
                status__in=['confirmed', 'completed'],
            ).filter(
                models.Q(show_schedule__movie=self.movie) | models.Q(movie=self.movie)
            ).exists()

        super().save(*args, **kwargs)
        if self.movie_id:
            self.movie.update_rating()

    def delete(self, *args, **kwargs):
        movie = self.movie
        super().delete(*args, **kwargs)
        movie.update_rating()


# ---------------------------------------------------------------------------
# Review reporting & moderation
# ---------------------------------------------------------------------------

class ReportedReview(models.Model):
    REASON_CHOICES = [
        ('spam', 'Spam'),
        ('offensive', 'Offensive Content'),
        ('harassment', 'Harassment'),
        ('false_info', 'False Information'),
        ('spoiler', 'Spoiler'),
        ('other', 'Other'),
    ]
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('reviewed', 'Reviewed'),
        ('dismissed', 'Dismissed'),
        ('resolved', 'Resolved'),
    ]

    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name='reports')
    reported_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='reported_reviews')
    reason = models.CharField(max_length=20, choices=REASON_CHOICES)
    comments = models.TextField(blank=True, help_text='Optional additional context from the reporter')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    reported_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        User, on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='moderated_reports',
        help_text='Admin who last acted on this report'
    )

    class Meta:
        ordering = ['-reported_at']
        # Prevent duplicate reports from the same user on the same review
        unique_together = ('review', 'reported_by')
        indexes = [
            models.Index(fields=['status', '-reported_at']),
        ]

    def __str__(self):
        return f'Report by {self.reported_by.username} on "{self.review}" [{self.status}]'


# ---------------------------------------------------------------------------
# Payment
# ---------------------------------------------------------------------------

import json as _json


class Payment(models.Model):
    GATEWAY_CHOICES = [
        ('razorpay', 'Razorpay'),
        ('stripe', 'Stripe'),
    ]
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('success', 'Success'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled'),
        ('refunded', 'Refunded'),
    ]

    # Relationships
    booking = models.OneToOneField(
        'Booking', on_delete=models.CASCADE,
        related_name='payment', null=True, blank=True
    )
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='payments'
    )
    show_schedule = models.ForeignKey(
        ShowSchedule, on_delete=models.CASCADE,
        related_name='payments', null=True, blank=True
    )

    # Gateway info
    gateway = models.CharField(max_length=20, choices=GATEWAY_CHOICES, default='razorpay')
    gateway_order_id = models.CharField(
        max_length=255, unique=True,
        help_text='Order ID returned by the payment gateway (e.g. order_xxx from Razorpay)'
    )
    gateway_payment_id = models.CharField(
        max_length=255, blank=True,
        help_text='Payment ID returned by the gateway after payment attempt'
    )
    gateway_signature = models.CharField(
        max_length=512, blank=True,
        help_text='Signature for server-side verification (Razorpay)'
    )

    # Transaction details — always calculated server-side
    amount = models.DecimalField(
        max_digits=10, decimal_places=2,
        help_text='Amount in base currency units (e.g. INR)'
    )
    amount_paise = models.PositiveIntegerField(
        default=0,
        help_text='Amount in smallest currency unit sent to gateway (paise for INR)'
    )
    currency = models.CharField(max_length=5, default='INR')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')

    # Audit
    gateway_response = models.TextField(
        blank=True,
        help_text='Raw JSON response from gateway — stored for audit/dispute resolution'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['gateway_order_id']),
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['status']),
        ]

    def __str__(self):
        return f'Payment {self.gateway_order_id} [{self.status}] ₹{self.amount}'

    def set_gateway_response(self, data: dict):
        """Safely store raw gateway response as JSON string."""
        self.gateway_response = _json.dumps(data)

    def get_gateway_response(self) -> dict:
        """Parse stored gateway response back to dict."""
        try:
            return _json.loads(self.gateway_response) if self.gateway_response else {}
        except Exception:
            return {}
