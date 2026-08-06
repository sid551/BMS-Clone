import csv
from datetime import datetime
from django.http import StreamingHttpResponse
from django.db.models import Q
from .analytics_service import (
    parse_date_input,
    get_revenue_analytics,
    get_booking_trends,
    get_theater_occupancy,
    get_most_booked_movies,
    get_top_performing_theaters,
    get_peak_booking_hours,
    get_cancellation_stats,
    get_refund_stats,
    get_user_growth_reports
)
from .models import Booking


class Echo:
    """An object that implements just the write method of the file-like interface."""
    def write(self, value):
        return value


def stream_csv_response(filename, header_row, rows_generator):
    """
    Creates a memory-efficient StreamingHttpResponse that yields CSV rows
    in constant O(1) memory buffers, ideal for 100,000+ records.
    """
    pseudo_buffer = Echo()
    writer = csv.writer(pseudo_buffer)

    def csv_data_stream():
        yield writer.writerow(header_row)
        for row in rows_generator:
            yield writer.writerow(row)

    response = StreamingHttpResponse(csv_data_stream(), content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


def generate_bookings_export_rows(start_dt=None, end_dt=None):
    """
    Yields detailed booking records using select_related() and iterator()
    to avoid loading all 100,000+ records into RAM at once.
    """
    qs = Booking.objects.select_related('user', 'movie', 'theater', 'show_schedule').order_by('-booked_at')
    if start_dt:
        qs = qs.filter(booked_at__gte=start_dt)
    if end_dt:
        qs = qs.filter(booked_at__lte=end_dt)

    # Use iterator(chunk_size=2000) for streaming high-scale database queries
    for b in qs.iterator(chunk_size=2000):
        movie_title = b.movie.title if b.movie else (b.show_schedule.movie.title if b.show_schedule and b.show_schedule.movie else 'N/A')
        theater_name = b.theater.name if b.theater else (b.show_schedule.theater.name if b.show_schedule and b.show_schedule.theater else 'N/A')
        show_time_str = b.show_schedule.show_time.strftime('%Y-%m-%d %H:%M') if (b.show_schedule and b.show_schedule.show_time) else 'N/A'
        booked_at_str = b.booked_at.strftime('%Y-%m-%d %H:%M:%S') if b.booked_at else ''

        yield [
            b.booking_reference,
            b.user.username if b.user else 'Guest',
            b.user.email if b.user else '',
            movie_title,
            theater_name,
            show_time_str,
            b.number_of_seats,
            float(b.total_price),
            b.get_status_display(),
            booked_at_str
        ]


def export_report_to_csv(report_type, start_date_str='', end_date_str=''):
    """
    Main CSV exporter controller that generates streaming CSV files for any report.
    """
    start_dt = parse_date_input(start_date_str, is_end=False)
    end_dt = parse_date_input(end_date_str, is_end=True)

    date_tag = f"_{start_date_str}_to_{end_date_str}" if (start_date_str or end_date_str) else "_all_time"

    if report_type == 'bookings':
        filename = f"bookings_report{date_tag}.csv"
        headers = ['Booking Ref', 'Username', 'Email', 'Movie', 'Theater', 'Show Time', 'Seats', 'Amount (INR)', 'Status', 'Booked At']
        generator = generate_bookings_export_rows(start_dt, end_dt)
        return stream_csv_response(filename, headers, generator)

    elif report_type == 'revenue':
        filename = f"revenue_summary{date_tag}.csv"
        headers = ['Metric Period', 'Revenue (INR)']
        stats = get_revenue_analytics(start_dt, end_dt)
        rows = [
            ['Daily Revenue (Today)', stats['daily_revenue']],
            ['Weekly Revenue (This Week)', stats['weekly_revenue']],
            ['Monthly Revenue (This Month)', stats['monthly_revenue']],
            ['Yearly Revenue (This Year)', stats['yearly_revenue']],
            ['Custom Date Range Total', stats['range_revenue']],
        ]
        return stream_csv_response(filename, headers, iter(rows))

    elif report_type == 'trends':
        filename = f"booking_trends{date_tag}.csv"
        headers = ['Date', 'Bookings Volume', 'Tickets Sold', 'Revenue (INR)']
        trends = get_booking_trends(start_dt, end_dt)
        rows = [[t['date'], t['total_bookings'], t['total_seats'], t['total_revenue']] for t in trends]
        return stream_csv_response(filename, headers, iter(rows))

    elif report_type == 'occupancy':
        filename = f"theater_occupancy{date_tag}.csv"
        headers = ['Theater Name', 'Location', 'Total Shows', 'Seats Booked', 'Total Capacity', 'Occupancy Rate (%)', 'Revenue (INR)']
        occ = get_theater_occupancy(start_dt, end_dt)
        rows = [[o['name'], o['location'], o['total_shows'], o['booked_seats'], o['total_capacity'], o['occupancy_pct'], o['total_revenue']] for o in occ]
        return stream_csv_response(filename, headers, iter(rows))

    elif report_type == 'top_movies':
        filename = f"top_movies{date_tag}.csv"
        headers = ['Rank', 'Movie Title', 'Status', 'Bookings Count', 'Tickets Sold', 'Revenue (INR)']
        movies = get_most_booked_movies(start_dt, end_dt, limit=50)
        rows = [[idx + 1, m['title'], m['status_display'], m['booking_count'], m['total_tickets'], m['total_revenue']] for idx, m in enumerate(movies)]
        return stream_csv_response(filename, headers, iter(rows))

    elif report_type == 'top_theaters':
        filename = f"top_theaters{date_tag}.csv"
        headers = ['Rank', 'Theater Name', 'Location', 'Bookings Count', 'Tickets Sold', 'Revenue (INR)']
        theaters = get_top_performing_theaters(start_dt, end_dt, limit=50)
        rows = [[idx + 1, t['name'], t['location'], t['booking_count'], t['total_tickets'], t['total_revenue']] for idx, t in enumerate(theaters)]
        return stream_csv_response(filename, headers, iter(rows))

    elif report_type == 'peak_hours':
        filename = f"peak_booking_hours{date_tag}.csv"
        headers = ['Hour of Day', 'Hour Label', 'Bookings Count', 'Seats Booked', 'Revenue (INR)']
        hours = get_peak_booking_hours(start_dt, end_dt)
        rows = [[h['hour'], h['label'], h['booking_count'], h['total_seats'], h['total_revenue']] for h in hours]
        return stream_csv_response(filename, headers, iter(rows))

    elif report_type == 'cancellations':
        filename = f"cancellation_statistics{date_tag}.csv"
        headers = ['Metric', 'Value']
        c = get_cancellation_stats(start_dt, end_dt)
        rows = [
            ['Total Bookings', c['total_bookings']],
            ['Confirmed Bookings', c['confirmed_bookings']],
            ['Pending Bookings', c['pending_bookings']],
            ['Cancelled Bookings', c['cancelled_bookings']],
            ['Cancelled Seats', c['cancelled_seats']],
            ['Cancellation Rate (%)', c['cancellation_rate']],
            ['Revenue Lost (INR)', c['lost_revenue']],
        ]
        return stream_csv_response(filename, headers, iter(rows))

    elif report_type == 'refunds':
        filename = f"refund_statistics{date_tag}.csv"
        headers = ['Metric', 'Value']
        r = get_refund_stats(start_dt, end_dt)
        rows = [
            ['Total Gateway Payments', r['total_payments']],
            ['Successful Payments', r['success_count']],
            ['Failed Attempts', r['failed_count']],
            ['Refunded Count', r['refunded_count']],
            ['Successful Revenue (INR)', r['total_success_amount']],
            ['Total Refunded Amount (INR)', r['total_refunded_amount']],
        ]
        return stream_csv_response(filename, headers, iter(rows))

    elif report_type == 'user_growth':
        filename = f"user_growth{date_tag}.csv"
        headers = ['Month Period', 'New Registered Users']
        growth = get_user_growth_reports(start_dt, end_dt)
        rows = [[g['period'], g['new_users']] for g in growth]
        return stream_csv_response(filename, headers, iter(rows))

    else:
        # Default fallback export all summary metrics
        filename = f"admin_analytics_summary{date_tag}.csv"
        headers = ['Report Category', 'Metric', 'Value']
        rows = [
            ['Revenue', 'Custom Date Range Revenue', get_revenue_analytics(start_dt, end_dt)['formatted_range']],
            ['Cancellations', 'Cancellation Rate', f"{get_cancellation_stats(start_dt, end_dt)['cancellation_rate']}%"],
            ['Refunds', 'Total Refunded', get_refund_stats(start_dt, end_dt)['formatted_refunded_amount']],
        ]
        return stream_csv_response(filename, headers, iter(rows))
