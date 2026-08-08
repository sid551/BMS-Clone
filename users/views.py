from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm
from .forms import UserRegisterForm, UserUpdateForm
from django.shortcuts import render, redirect
from django.contrib.auth import login, authenticate
from django.contrib.auth.decorators import login_required
from movies.models import Movie, Booking, Payment


def home(request):
    movies = Movie.objects.all()
    return render(request, 'home.html', {'movies': movies})


def register(request):
    if request.method == 'POST':
        form = UserRegisterForm(request.POST)
        if form.is_valid():
            form.save()
            username = form.cleaned_data.get('username')
            password = form.cleaned_data.get('password1')
            user = authenticate(username=username, password=password)
            login(request, user)
            return redirect('profile')
    else:
        form = UserRegisterForm()
    return render(request, 'users/register.html', {'form': form})


def login_view(request):
    next_url = request.POST.get('next') or request.GET.get('next') or '/'
    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            return redirect(next_url)
    else:
        form = AuthenticationForm()
    return render(request, 'users/login.html', {'form': form})


@login_required
def profile(request):
    bookings_qs = (
        Booking.objects
        .filter(user=request.user)
        .select_related('movie', 'theater', 'show_schedule__movie', 'show_schedule__theater')
        .prefetch_related('booked_seats__seat')
        .order_by('-booked_at')
    )

    payments = (
        Payment.objects
        .filter(user=request.user)
        .select_related('booking', 'show_schedule__movie', 'show_schedule__theater')
        .order_by('-created_at')
    )

    # Pre-compute ticket URLs and seat lists in Python to avoid N+1 and slow Cloudinary calls in template
    bookings = []
    for booking in bookings_qs:
        # Seat list
        booked_seats = booking.booked_seats.all()
        if booked_seats:
            seat_str = ', '.join(bs.seat.seat_number for bs in booked_seats)
        elif booking.seat_id:
            seat_str = booking.seat.seat_number if booking.seat else str(booking.number_of_seats)
        else:
            seat_str = f'{booking.number_of_seats} seat{"s" if booking.number_of_seats != 1 else ""}'

        # Ticket URL — only access if field has a name (avoids Cloudinary call)
        ticket_url = None
        has_ticket = bool(booking.ticket and booking.ticket.name)
        if has_ticket:
            try:
                ticket_url = booking.ticket.url
            except Exception:
                ticket_url = None
                has_ticket = False

        bookings.append({
            'obj': booking,
            'seat_str': seat_str,
            'has_ticket': has_ticket,
            'ticket_url': ticket_url,
        })

    if request.method == 'POST':
        u_form = UserUpdateForm(request.POST, instance=request.user)
        if u_form.is_valid():
            u_form.save()
            return redirect('profile')
    else:
        u_form = UserUpdateForm(instance=request.user)

    return render(request, 'users/profile.html', {
        'u_form': u_form,
        'bookings': bookings,
        'payments': payments,
    })


@login_required
def reset_password(request):
    if request.method == 'POST':
        form = PasswordChangeForm(user=request.user, data=request.POST)
        if form.is_valid():
            form.save()
            return redirect('login')
    else:
        form = PasswordChangeForm(user=request.user)
    return render(request, 'users/reset_password.html', {'form': form})