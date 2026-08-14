from django.urls import path
from . import views

urlpatterns = [
    path('', views.movie_list, name='movie_list'),
    path('<int:movie_id>/', views.movie_detail, name='movie_detail'),
    path('<int:movie_id>/theaters', views.theater_list, name='theater_list'),
    path('theater/<int:theater_id>/seats/book/', views.book_seats, name='book_seats'),
    path('<int:movie_id>/review/add/', views.add_review, name='add_review'),
    path('review/<int:review_id>/edit/', views.edit_review, name='edit_review'),
    path('review/<int:review_id>/delete/', views.delete_review, name='delete_review'),
    path('review/<int:review_id>/report/', views.report_review, name='report_review'),
    path('schedule/<int:schedule_id>/seats/', views.seat_map_api, name='seat_map_api'),
    path('schedule/<int:schedule_id>/reserve/', views.reserve_seats_api, name='reserve_seats_api'),
    path('schedule/<int:schedule_id>/release/', views.release_seats_api, name='release_seats_api'),
    path('schedule/<int:schedule_id>/reservation-status/', views.reservation_status_api, name='reservation_status_api'),
    path('schedule/<int:schedule_id>/confirm-booking/', views.confirm_booking_api, name='confirm_booking_api'),
    path('schedule/<int:schedule_id>/create-payment-order/', views.create_payment_order_api, name='create_payment_order_api'),
    path('payment/<int:payment_id>/status/', views.get_payment_status_api, name='get_payment_status_api'),
    path('payment/verify/', views.verify_payment_api, name='verify_payment_api'),
    path('payment/failed/', views.record_payment_failure_api, name='record_payment_failure_api'),
    path('payment/webhook/razorpay/', views.razorpay_webhook, name='razorpay_webhook'),

    # Ticket PDF & Verification & Cancellation
    path('booking/<str:booking_reference>/ticket/', views.download_ticket_pdf, name='download_ticket_pdf'),
    path('booking/<str:booking_reference>/verify/', views.verify_ticket, name='verify_ticket'),
    path('booking/<str:booking_reference>/resend-email/', views.resend_booking_email, name='resend_booking_email'),
    path('booking/<str:booking_reference>/cancel/', views.cancel_booking_user_view, name='cancel_booking'),
    path('manage/bookings/<int:booking_id>/cancel-refund/', views.admin_cancel_and_refund_view, name='admin_cancel_refund'),


    # Custom Admin Management Routes
    path('manage/', views.admin_dashboard, name='admin_dashboard'),
    path('manage/export/csv/', views.admin_export_csv, name='admin_export_csv'),

    path('manage/movies/', views.admin_manage_movies, name='admin_manage_movies'),
    path('manage/movies/add/', views.admin_movie_form, name='admin_movie_add'),
    path('manage/movies/<int:movie_id>/edit/', views.admin_movie_form, name='admin_movie_edit'),
    path('manage/movies/<int:movie_id>/delete/', views.admin_movie_delete, name='admin_movie_delete'),

    # Quick Taxonomy API Routes
    path('manage/api/genre/add/', views.api_quick_add_genre, name='api_quick_add_genre'),
    path('manage/api/genre/<int:genre_id>/delete/', views.api_quick_delete_genre, name='api_quick_delete_genre'),
    path('manage/api/language/add/', views.api_quick_add_language, name='api_quick_add_language'),
    path('manage/api/language/<int:language_id>/delete/', views.api_quick_delete_language, name='api_quick_delete_language'),
    path('manage/api/cast/add/', views.api_quick_add_cast, name='api_quick_add_cast'),
    path('manage/api/cast/<int:cast_id>/delete/', views.api_quick_delete_cast, name='api_quick_delete_cast'),
    path('manage/api/taxonomies/bulk-delete/', views.api_bulk_delete_taxonomies, name='api_bulk_delete_taxonomies'),
    path('manage/api/movie/<int:movie_id>/unassign-taxonomy/', views.api_unassign_movie_taxonomy, name='api_unassign_movie_taxonomy'),
    path('manage/api/theater/<int:theater_id>/screens/', views.api_get_theater_screens, name='api_get_theater_screens'),




    path('manage/theaters/', views.admin_manage_theaters, name='admin_manage_theaters'),
    path('manage/theaters/add/', views.admin_theater_form, name='admin_theater_add'),
    path('manage/theaters/<int:theater_id>/edit/', views.admin_theater_form, name='admin_theater_edit'),
    path('manage/theaters/<int:theater_id>/delete/', views.admin_theater_delete, name='admin_theater_delete'),
    path('manage/schedules/', views.admin_manage_schedules, name='admin_manage_schedules'),
    path('manage/schedules/add/', views.admin_schedule_form, name='admin_schedule_add'),
    path('manage/schedules/<int:schedule_id>/edit/', views.admin_schedule_form, name='admin_schedule_edit'),
    path('manage/schedules/<int:schedule_id>/delete/', views.admin_schedule_delete, name='admin_schedule_delete'),
    path('manage/reports/', views.admin_manage_reports, name='admin_manage_reports'),
    path('manage/reports/<int:report_id>/resolve/', views.admin_resolve_report, name='admin_resolve_report'),
    path('manage/taxonomies/', views.admin_manage_taxonomies, name='admin_manage_taxonomies'),
    path('manage/screens/', views.admin_manage_screens, name='admin_manage_screens'),
    path('manage/screens/add/', views.admin_screen_form, name='admin_screen_add'),
    path('manage/screens/<int:screen_id>/edit/', views.admin_screen_form, name='admin_screen_edit'),
    path('manage/screens/<int:screen_id>/delete/', views.admin_screen_delete, name='admin_screen_delete'),
    path('manage/screens/<int:screen_id>/seat-map/', views.admin_screen_seat_map, name='admin_screen_seat_map'),
    path('manage/movies/<int:movie_id>/gallery/', views.admin_movie_gallery, name='admin_movie_gallery'),

    path('manage/schedules/bulk/', views.admin_bulk_schedule_add, name='admin_bulk_schedule_add'),
    path('manage/bookings/', views.admin_manage_bookings, name='admin_manage_bookings'),
    path('manage/bookings/<int:booking_id>/action/', views.admin_booking_action, name='admin_booking_action'),
    path('manage/seats/', views.admin_manage_seats, name='admin_manage_seats'),
    path('manage/seats/update/', views.admin_update_seat_status, name='admin_update_seat_status'),
    path('manage/seats/toggle-ajax/', views.admin_toggle_seat_ajax, name='admin_toggle_seat_ajax'),
]




