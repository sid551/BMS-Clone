from datetime import datetime, date, time
import logging
from django.db.models import Count, Sum, Avg, F, Q, ExpressionWrapper, FloatField, DecimalField, Value
from django.db.models.functions import TruncDate, TruncWeek, TruncMonth, TruncYear, ExtractHour, Coalesce
from django.utils import timezone
from django.contrib.auth import get_user_model

from .models import Movie, Theater, ShowSchedule, Booking, Payment, Seat, Screen, BookingSeat, ReportedReview

logger = logging.getLogger(__name__)
User = get_user_model()


def parse_date_input(date_str, is_end=False):
    """
    Safely parse date string 'YYYY-MM-DD' into timezone-aware datetime object.
    If date_str is invalid or missing, returns None safely without raising exceptions.
    """
    if not date_str or not isinstance(date_str, str):
        return None
    try:
        parsed_d = datetime.strptime(date_str.strip(), '%Y-%m-%d').date()
        if is_end:
            dt = datetime.combine(parsed_d, time.max)
        else:
            dt = datetime.combine(parsed_d, time.min)
        return timezone.make_aware(dt) if timezone.is_naive(dt) else dt
    except (ValueError, TypeError, AttributeError):
        logger.warning(f"Invalid date string format provided: '{date_str}'")
        return None


def get_revenue_analytics(start_dt=None, end_dt=None):
    """
    Calculates Total Revenue across daily, weekly, monthly, yearly, and custom date range.
    """
    try:
        now = timezone.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = today_start - timezone.timedelta(days=now.weekday())
        month_start = today_start.replace(day=1)
        year_start = today_start.replace(month=1, day=1)

        confirmed_qs = Booking.objects.filter(status__in=['confirmed', 'completed'])

        daily_revenue = confirmed_qs.filter(booked_at__gte=today_start).aggregate(
            total=Coalesce(Sum('total_price'), Value(0, output_field=DecimalField()))
        )['total']

        weekly_revenue = confirmed_qs.filter(booked_at__gte=week_start).aggregate(
            total=Coalesce(Sum('total_price'), Value(0, output_field=DecimalField()))
        )['total']

        monthly_revenue = confirmed_qs.filter(booked_at__gte=month_start).aggregate(
            total=Coalesce(Sum('total_price'), Value(0, output_field=DecimalField()))
        )['total']

        yearly_revenue = confirmed_qs.filter(booked_at__gte=year_start).aggregate(
            total=Coalesce(Sum('total_price'), Value(0, output_field=DecimalField()))
        )['total']

        filtered_qs = confirmed_qs
        if start_dt:
            filtered_qs = filtered_qs.filter(booked_at__gte=start_dt)
        if end_dt:
            filtered_qs = filtered_qs.filter(booked_at__lte=end_dt)

        range_revenue = filtered_qs.aggregate(
            total=Coalesce(Sum('total_price'), Value(0, output_field=DecimalField()))
        )['total']

        return {
            'daily_revenue': float(daily_revenue),
            'weekly_revenue': float(weekly_revenue),
            'monthly_revenue': float(monthly_revenue),
            'yearly_revenue': float(yearly_revenue),
            'range_revenue': float(range_revenue),
            'formatted_daily': f"₹{daily_revenue:,.2f}",
            'formatted_weekly': f"₹{weekly_revenue:,.2f}",
            'formatted_monthly': f"₹{monthly_revenue:,.2f}",
            'formatted_yearly': f"₹{yearly_revenue:,.2f}",
            'formatted_range': f"₹{range_revenue:,.2f}",
        }
    except Exception as e:
        logger.error(f"Error in get_revenue_analytics: {e}")
        return {
            'daily_revenue': 0.0, 'weekly_revenue': 0.0, 'monthly_revenue': 0.0,
            'yearly_revenue': 0.0, 'range_revenue': 0.0,
            'formatted_daily': "₹0.00", 'formatted_weekly': "₹0.00",
            'formatted_monthly': "₹0.00", 'formatted_yearly': "₹0.00",
            'formatted_range': "₹0.00",
        }


def get_booking_trends(start_dt=None, end_dt=None):
    """
    Groups bookings by date (TruncDate) to calculate daily booking count and revenue trend.
    """
    try:
        qs = Booking.objects.filter(status__in=['confirmed', 'completed'])
        if start_dt:
            qs = qs.filter(booked_at__gte=start_dt)
        if end_dt:
            qs = qs.filter(booked_at__lte=end_dt)

        trends = (
            qs.annotate(date=TruncDate('booked_at'))
            .values('date')
            .annotate(
                total_bookings=Count('id'),
                total_seats=Coalesce(Sum('number_of_seats'), 0),
                total_revenue=Coalesce(Sum('total_price'), Value(0, output_field=DecimalField()))
            )
            .order_by('date')
        )

        result = []
        for item in trends:
            d_str = item['date'].strftime('%Y-%m-%d') if item['date'] else ''
            result.append({
                'date': d_str,
                'label': item['date'].strftime('%b %d') if item['date'] else '',
                'total_bookings': item['total_bookings'],
                'total_seats': item['total_seats'],
                'total_revenue': float(item['total_revenue']),
            })
        return result
    except Exception as e:
        logger.error(f"Error in get_booking_trends: {e}")
        return []


def get_theater_occupancy(start_dt=None, end_dt=None):
    """
    Calculates occupancy percentage for each theater using Django ORM aggregations.
    """
    try:
        theaters = Theater.objects.all()

        booking_q = Q(schedules__bookings__status__in=['confirmed', 'completed']) | Q(booking__status__in=['confirmed', 'completed'])
        schedule_q = Q()
        if start_dt:
            booking_q &= (Q(schedules__bookings__booked_at__gte=start_dt) | Q(booking__booked_at__gte=start_dt))
            schedule_q &= Q(schedules__show_time__gte=start_dt)
        if end_dt:
            booking_q &= (Q(schedules__bookings__booked_at__lte=end_dt) | Q(booking__booked_at__lte=end_dt))
            schedule_q &= Q(schedules__show_time__lte=end_dt)

        annotated = theaters.annotate(
            total_shows=Count('schedules', filter=schedule_q, distinct=True),
            booked_seats=Coalesce(Sum('schedules__bookings__number_of_seats', filter=booking_q), 0),
            total_revenue=Coalesce(Sum('schedules__bookings__total_price', filter=booking_q), Value(0, output_field=DecimalField())),
        )

        result = []
        for t in annotated:
            capacity = (t.total_seats or 0) * (t.total_shows or 0)
            occupancy_pct = round((t.booked_seats / capacity * 100), 2) if capacity > 0 else 0.0
            result.append({
                'id': t.id,
                'name': t.name,
                'location': t.location,
                'total_seats_per_show': t.total_seats,
                'total_shows': t.total_shows,
                'booked_seats': t.booked_seats,
                'total_capacity': capacity,
                'occupancy_pct': occupancy_pct,
                'total_revenue': float(t.total_revenue),
                'formatted_revenue': f"₹{t.total_revenue:,.2f}",
            })

        result.sort(key=lambda x: x['occupancy_pct'], reverse=True)
        return result
    except Exception as e:
        logger.error(f"Error in get_theater_occupancy: {e}")
        return []


def get_most_booked_movies(start_dt=None, end_dt=None, limit=5):
    """
    Returns top movies annotated with booking count, ticket volume, and total revenue.
    """
    try:
        booking_q = Q(schedules__bookings__status__in=['confirmed', 'completed']) | Q(booking__status__in=['confirmed', 'completed'])
        if start_dt:
            booking_q &= (Q(schedules__bookings__booked_at__gte=start_dt) | Q(booking__booked_at__gte=start_dt))
        if end_dt:
            booking_q &= (Q(schedules__bookings__booked_at__lte=end_dt) | Q(booking__booked_at__lte=end_dt))

        movies = (
            Movie.objects.annotate(
                booking_count=Count('schedules__bookings', filter=booking_q, distinct=True),
                total_tickets=Coalesce(Sum('schedules__bookings__number_of_seats', filter=booking_q), 0),
                total_revenue=Coalesce(Sum('schedules__bookings__total_price', filter=booking_q), Value(0, output_field=DecimalField()))
            )
            .order_by('-total_tickets', '-total_revenue')[:limit]
        )

        result = []
        for m in movies:
            result.append({
                'id': m.id,
                'title': m.title,
                'poster_url': m.poster_url,
                'status_display': m.get_status_display(),
                'booking_count': m.booking_count,
                'total_tickets': m.total_tickets,
                'total_revenue': float(m.total_revenue),
                'formatted_revenue': f"₹{m.total_revenue:,.2f}",
            })
        return result
    except Exception as e:
        logger.error(f"Error in get_most_booked_movies: {e}")
        return []


def get_top_performing_theaters(start_dt=None, end_dt=None, limit=5):
    """
    Returns top theaters annotated with revenue and total bookings.
    """
    try:
        booking_q = Q(schedules__bookings__status__in=['confirmed', 'completed']) | Q(booking__status__in=['confirmed', 'completed'])
        if start_dt:
            booking_q &= (Q(schedules__bookings__booked_at__gte=start_dt) | Q(booking__booked_at__gte=start_dt))
        if end_dt:
            booking_q &= (Q(schedules__bookings__booked_at__lte=end_dt) | Q(booking__booked_at__lte=end_dt))

        theaters = (
            Theater.objects.annotate(
                booking_count=Count('schedules__bookings', filter=booking_q, distinct=True),
                total_tickets=Coalesce(Sum('schedules__bookings__number_of_seats', filter=booking_q), 0),
                total_revenue=Coalesce(Sum('schedules__bookings__total_price', filter=booking_q), Value(0, output_field=DecimalField()))
            )
            .order_by('-total_revenue', '-booking_count')[:limit]
        )

        result = []
        for t in theaters:
            result.append({
                'id': t.id,
                'name': t.name,
                'location': t.location,
                'booking_count': t.booking_count,
                'total_tickets': t.total_tickets,
                'total_revenue': float(t.total_revenue),
                'formatted_revenue': f"₹{t.total_revenue:,.2f}",
            })
        return result
    except Exception as e:
        logger.error(f"Error in get_top_performing_theaters: {e}")
        return []


def get_peak_booking_hours(start_dt=None, end_dt=None):
    """
    Aggregates booking activity by hour of day (0-23) using ExtractHour.
    """
    try:
        qs = Booking.objects.all()
        if start_dt:
            qs = qs.filter(booked_at__gte=start_dt)
        if end_dt:
            qs = qs.filter(booked_at__lte=end_dt)

        hours_data = (
            qs.annotate(hour=ExtractHour('booked_at'))
            .values('hour')
            .annotate(
                booking_count=Count('id'),
                total_seats=Coalesce(Sum('number_of_seats'), 0),
                total_revenue=Coalesce(
                    Sum('total_price', filter=Q(status__in=['confirmed', 'completed'])),
                    Value(0, output_field=DecimalField())
                )
            )
            .order_by('hour')
        )

        hour_dict = {item['hour']: item for item in hours_data if item['hour'] is not None}

        full_hours = []
        for h in range(24):
            item = hour_dict.get(h, {'booking_count': 0, 'total_seats': 0, 'total_revenue': 0})
            label = f"{h:02d}:00"
            full_hours.append({
                'hour': h,
                'label': label,
                'booking_count': item['booking_count'],
                'total_seats': item['total_seats'],
                'total_revenue': float(item['total_revenue']),
            })
        return full_hours
    except Exception as e:
        logger.error(f"Error in get_peak_booking_hours: {e}")
        return [{'hour': h, 'label': f"{h:02d}:00", 'booking_count': 0, 'total_seats': 0, 'total_revenue': 0.0} for h in range(24)]


def get_cancellation_stats(start_dt=None, end_dt=None):
    """
    Calculates total bookings, cancelled bookings count, cancellation rate %, and lost revenue.
    """
    try:
        qs = Booking.objects.all()
        if start_dt:
            qs = qs.filter(booked_at__gte=start_dt)
        if end_dt:
            qs = qs.filter(booked_at__lte=end_dt)

        stats = qs.aggregate(
            total_bookings=Count('id'),
            cancelled_bookings=Count('id', filter=Q(status='cancelled')),
            confirmed_bookings=Count('id', filter=Q(status__in=['confirmed', 'completed'])),
            pending_bookings=Count('id', filter=Q(status='pending')),
            lost_revenue=Coalesce(Sum('total_price', filter=Q(status='cancelled')), Value(0, output_field=DecimalField())),
            cancelled_seats=Coalesce(Sum('number_of_seats', filter=Q(status='cancelled')), 0)
        )

        total = stats['total_bookings'] or 0
        cancelled = stats['cancelled_bookings'] or 0
        cancellation_rate = round((cancelled / total * 100), 2) if total > 0 else 0.0
        lost_rev = float(stats['lost_revenue'])

        return {
            'total_bookings': total,
            'cancelled_bookings': cancelled,
            'confirmed_bookings': stats['confirmed_bookings'] or 0,
            'pending_bookings': stats['pending_bookings'] or 0,
            'cancelled_seats': stats['cancelled_seats'] or 0,
            'lost_revenue': lost_rev,
            'formatted_lost_revenue': f"₹{lost_rev:,.2f}",
            'cancellation_rate': cancellation_rate,
        }
    except Exception as e:
        logger.error(f"Error in get_cancellation_stats: {e}")
        return {
            'total_bookings': 0, 'cancelled_bookings': 0, 'confirmed_bookings': 0,
            'pending_bookings': 0, 'cancelled_seats': 0, 'lost_revenue': 0.0,
            'formatted_lost_revenue': "₹0.00", 'cancellation_rate': 0.0
        }


def get_refund_stats(start_dt=None, end_dt=None):
    """
    Calculates refund statistics from Payment objects.
    """
    try:
        qs = Payment.objects.all()
        if start_dt:
            qs = qs.filter(created_at__gte=start_dt)
        if end_dt:
            qs = qs.filter(created_at__lte=end_dt)

        stats = qs.aggregate(
            total_payments=Count('id'),
            refunded_count=Count('id', filter=Q(status='refunded')),
            failed_count=Count('id', filter=Q(status='failed')),
            cancelled_count=Count('id', filter=Q(status='cancelled')),
            success_count=Count('id', filter=Q(status='success')),
            total_refunded_amount=Coalesce(Sum('amount', filter=Q(status='refunded')), Value(0, output_field=DecimalField())),
            total_success_amount=Coalesce(Sum('amount', filter=Q(status='success')), Value(0, output_field=DecimalField()))
        )

        refunded_amt = float(stats['total_refunded_amount'])
        success_amt = float(stats['total_success_amount'])

        return {
            'total_payments': stats['total_payments'] or 0,
            'refunded_count': stats['refunded_count'] or 0,
            'failed_count': stats['failed_count'] or 0,
            'cancelled_count': stats['cancelled_count'] or 0,
            'success_count': stats['success_count'] or 0,
            'total_refunded_amount': refunded_amt,
            'formatted_refunded_amount': f"₹{refunded_amt:,.2f}",
            'total_success_amount': success_amt,
            'formatted_success_amount': f"₹{success_amt:,.2f}",
        }
    except Exception as e:
        logger.error(f"Error in get_refund_stats: {e}")
        return {
            'total_payments': 0, 'refunded_count': 0, 'failed_count': 0,
            'cancelled_count': 0, 'success_count': 0, 'total_refunded_amount': 0.0,
            'formatted_refunded_amount': "₹0.00", 'total_success_amount': 0.0,
            'formatted_success_amount': "₹0.00"
        }


def get_user_growth_reports(start_dt=None, end_dt=None):
    """
    Aggregates user registrations over time using TruncMonth.
    """
    try:
        qs = User.objects.all()
        if start_dt:
            qs = qs.filter(date_joined__gte=start_dt)
        if end_dt:
            qs = qs.filter(date_joined__lte=end_dt)

        growth = (
            qs.annotate(period=TruncMonth('date_joined'))
            .values('period')
            .annotate(new_users=Count('id'))
            .order_by('period')
        )

        result = []
        for item in growth:
            p_str = item['period'].strftime('%b %Y') if item['period'] else 'Unknown'
            result.append({
                'period': p_str,
                'new_users': item['new_users'],
            })

        if not result:
            now = timezone.now()
            tot_users = User.objects.count()
            result = [{
                'period': now.strftime('%b %Y'),
                'new_users': tot_users,
            }]

        return result
    except Exception as e:
        logger.error(f"Error in get_user_growth_reports: {e}")
        return [{'period': timezone.now().strftime('%b %Y'), 'new_users': 0}]


def get_full_admin_analytics(start_date_str='', end_date_str=''):
    """
    Master coordinator function that executes all ORM business analytics
    and returns a clean context dictionary ready for template rendering.
    """
    start_dt = parse_date_input(start_date_str, is_end=False)
    end_dt = parse_date_input(end_date_str, is_end=True)

    now = timezone.now()

    # Overall Count Summaries
    movie_count = Movie.objects.count()
    theater_count = Theater.objects.count()
    schedule_count = ShowSchedule.objects.count()
    user_count = User.objects.count()
    booking_count = Booking.objects.count()
    active_shows_count = ShowSchedule.objects.filter(show_time__gte=now).count()
    pending_reports = ReportedReview.objects.filter(status='pending').count()

    # Analytics Functions
    revenue_stats = get_revenue_analytics(start_dt, end_dt)
    booking_trends = get_booking_trends(start_dt, end_dt)
    theater_occupancy = get_theater_occupancy(start_dt, end_dt)
    top_movies = get_most_booked_movies(start_dt, end_dt, limit=5)
    top_theaters = get_top_performing_theaters(start_dt, end_dt, limit=5)
    peak_hours = get_peak_booking_hours(start_dt, end_dt)
    cancellation_stats = get_cancellation_stats(start_dt, end_dt)
    refund_stats = get_refund_stats(start_dt, end_dt)
    user_growth = get_user_growth_reports(start_dt, end_dt)

    # Activity Feeds
    recent_movies = Movie.objects.all().order_by('-id')[:5]
    recent_bookings = Booking.objects.select_related('user', 'movie', 'show_schedule', 'theater').order_by('-booked_at')[:5]
    recent_reports = ReportedReview.objects.select_related('review', 'reported_by').filter(status='pending').order_by('-reported_at')[:5]

    return {
        # Overall Counts
        'movie_count': movie_count,
        'theater_count': theater_count,
        'schedule_count': schedule_count,
        'user_count': user_count,
        'booking_count': booking_count,
        'active_shows': active_shows_count,
        'pending_reports': pending_reports,

        # Filter Inputs
        'start_date': start_date_str,
        'end_date': end_date_str,

        # Analytics Objects
        'revenue_stats': revenue_stats,
        'booking_trends': booking_trends,
        'theater_occupancy': theater_occupancy,
        'top_movies': top_movies,
        'top_theaters': top_theaters,
        'peak_hours': peak_hours,
        'cancellation_stats': cancellation_stats,
        'refund_stats': refund_stats,
        'user_growth': user_growth,
        'user_growth_reports': user_growth,

        # Recent Feeds
        'recent_movies': recent_movies,
        'recent_bookings': recent_bookings,
        'recent_reports': recent_reports,
    }
