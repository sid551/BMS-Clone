# Admin Credentials & Business Insights Report

## Authorized Admin Credentials

- **Admin Dashboard URL**: `http://localhost:8000/movies/manage/`
- **Username**: `admin`
- **Password**: `admin123`
- **Email**: `admin@bookmyseat.com`
- **Role**: Superuser / Authorized Admin Staff

---

## Access Control & Permission System
Access to all `/movies/manage/*` admin dashboard routes and reporting tools is strictly guarded using Django's built-in authentication and permission system via the custom `@staff_or_admin_required` view decorator.

```python
def is_staff_user(user):
    return bool(user and user.is_authenticated and (user.is_staff or user.is_superuser))
```

Unauthenticated users are redirected to `/login/?next=...`. Non-staff authenticated users receive an HTTP 403 Forbidden page.

---

## Analytics & Business Insights Features

1. **Total Revenue Aggregations**:
   - Daily Revenue (Today)
   - Weekly Revenue (This Week)
   - Monthly Revenue (This Month)
   - Yearly Revenue (This Year)
   - Custom Date Range Revenue

2. **Booking Trends**:
   - Grouped by `TruncDate('booked_at')` to analyze daily booking volume and ticket sales over time.

3. **Theater Occupancy Percentage**:
   - Calculates `(Booked Seats / Maximum Capacity) * 100` per theater venue.

4. **Most Booked Movies**:
   - Ranked by ticket volume and gross revenue using ORM annotations.

5. **Top-Performing Theaters**:
   - Ranked venue leaderboards.

6. **Peak Booking Hours**:
   - Hourly distribution (0-23) using `ExtractHour('booked_at')`.

7. **Cancellation & Refund Statistics**:
   - Cancellation rate %, cancelled seat count, lost revenue, and payment gateway audit.

8. **User Growth Reports**:
   - Monthly registration trends using `TruncMonth('date_joined')`.

9. **Custom Date Range Filtering**:
   - Start and end date range filters applicable across all dashboard charts and metrics.

10. **Streaming CSV Reports**:
    - Low-memory streaming CSV export endpoints for all reports using `StreamingHttpResponse` and `iterator(chunk_size=2000)`.

---

## Query Optimization & Database Indexing (100,000+ Bookings Scale)

All calculations run directly inside the database via Django ORM aggregations (`Sum`, `Count`, `Avg`, `TruncDate`, `ExtractHour`, `Coalesce`).

### Configured B-Tree Indexes (`movies/models.py`)
- `Booking`: `booked_at`, `status`, `[user, -booked_at]`, `[status, -booked_at]`, `[movie, status]`, `[theater, status]`, `[show_schedule, status]`, `booking_reference`.
- `ShowSchedule`: `show_time`, `[movie, show_time]`, `[theater, show_time]`.
- `ShowSeat`: `[show_schedule, status]`, `reserved_until`.
- `Payment`: `created_at`, `status`, `gateway_order_id`, `[user, -created_at]`.
