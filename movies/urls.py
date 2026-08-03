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

    # Custom Admin Management Routes
    path('manage/', views.admin_dashboard, name='admin_dashboard'),
    path('manage/movies/', views.admin_manage_movies, name='admin_manage_movies'),
    path('manage/movies/add/', views.admin_movie_form, name='admin_movie_add'),
    path('manage/movies/<int:movie_id>/edit/', views.admin_movie_form, name='admin_movie_edit'),
    path('manage/movies/<int:movie_id>/delete/', views.admin_movie_delete, name='admin_movie_delete'),
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




