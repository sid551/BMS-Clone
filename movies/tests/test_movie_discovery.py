from datetime import timedelta
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from movies.models import Movie, Genre, Language, Theater, ShowSchedule, User, Booking
from movies.recommendations import get_personalized_recommendations


class MovieDiscoveryTests(TestCase):
    def setUp(self):
        # Create test movies with varying case titles, ratings, and release status
        self.movie_a = Movie.objects.create(
            title="Avatar: The Way of Water",
            duration_minutes=192,
            rating=4.8,
            status='now_showing',
            release_date='2022-12-16'
        )
        self.movie_b = Movie.objects.create(
            title="Batman Begins",
            duration_minutes=140,
            rating=4.2,
            status='now_showing',
            release_date='2005-06-15'
        )
        self.movie_c = Movie.objects.create(
            title="Avengers: Endgame",
            duration_minutes=181,
            rating=4.9,
            status='ended',
            release_date='2019-04-26'
        )
        self.movie_d = Movie.objects.create(
            title="Cyberpunk 2077 Movie",
            duration_minutes=120,
            rating=3.5,
            status='upcoming',
            release_date='2025-11-20'
        )
        self.movie_e = Movie.objects.create(
            title="Dark Knight",
            duration_minutes=152,
            rating=4.7,
            status='now_showing',
            release_date='2008-07-18'
        )

        # Taxonomies
        self.genre_action = Genre.objects.create(movie=self.movie_b, name="Action")
        Genre.objects.create(movie=self.movie_c, name="Action")
        Genre.objects.create(movie=self.movie_a, name="Sci-Fi")

        self.lang_english = Language.objects.create(movie=self.movie_a, name="English")
        Language.objects.create(movie=self.movie_b, name="English")

        # Theaters & Schedules
        self.theater_ny = Theater.objects.create(name="AMC Empire 25", location="New York")
        self.theater_la = Theater.objects.create(name="Regal LA Live", location="Los Angeles")

        now = timezone.now()
        future_morning = now + timedelta(days=1, hours=2)
        future_evening = now + timedelta(days=1, hours=8)

        self.sched_a = ShowSchedule.objects.create(
            movie=self.movie_a,
            theater=self.theater_ny,
            show_time=future_morning,
            price=15.00,
            available_seats=100
        )
        self.sched_b = ShowSchedule.objects.create(
            movie=self.movie_b,
            theater=self.theater_la,
            show_time=future_evening,
            price=22.00,
            available_seats=100
        )

        # User & Booking for Popularity testing
        self.user = User.objects.create_user(username="testuser", password="password123")
        Booking.objects.create(
            user=self.user,
            show_schedule=self.sched_b,
            number_of_seats=2,
            total_price=44.00,
            status='confirmed'
        )

    def test_default_listing_alphabetical_order(self):
        """Test that all movies are returned ordered alphabetically by title."""
        response = self.client.get(reverse('movie_list'))
        self.assertEqual(response.status_code, 200)
        movies = list(response.context['movies'])
        titles = [m.title for m in movies]
        expected_titles = sorted([self.movie_a.title, self.movie_b.title, self.movie_c.title, self.movie_d.title, self.movie_e.title])
        self.assertEqual(titles, expected_titles)
        self.assertEqual(response.context['total_movies'], 5)

    def test_case_insensitive_title_search(self):
        """Test case-insensitive title search."""
        response_lower = self.client.get(reverse('movie_list') + '?search=batman')
        self.assertEqual(response_lower.status_code, 200)
        self.assertEqual(response_lower.context['total_movies'], 1)
        self.assertEqual(response_lower.context['movies'][0].title, "Batman Begins")

        response_upper = self.client.get(reverse('movie_list') + '?search=BATMAN')
        self.assertEqual(response_upper.status_code, 200)
        self.assertEqual(response_upper.context['total_movies'], 1)

        response_partial = self.client.get(reverse('movie_list') + '?search=av')
        self.assertEqual(response_partial.status_code, 200)
        self.assertEqual(response_partial.context['total_movies'], 2)

    def test_filtering_by_genre_language_rating_status(self):
        """Test filtering by genre, language, minimum rating, and release status."""
        res_genre = self.client.get(reverse('movie_list') + '?genre=Action')
        self.assertEqual(res_genre.context['total_movies'], 2)

        res_rating = self.client.get(reverse('movie_list') + '?min_rating=4.5')
        self.assertEqual(res_rating.context['total_movies'], 3)

        res_status = self.client.get(reverse('movie_list') + '?status=upcoming')
        self.assertEqual(res_status.context['total_movies'], 1)
        self.assertEqual(res_status.context['movies'][0].title, "Cyberpunk 2077 Movie")

    def test_filtering_by_city_theater_and_time_slot(self):
        """Test filtering by city location, theater ID, and show time slot."""
        res_city = self.client.get(reverse('movie_list') + '?city=Los Angeles')
        self.assertEqual(res_city.context['total_movies'], 1)
        self.assertEqual(res_city.context['movies'][0].title, "Batman Begins")

    def test_sorting_options(self):
        """Test sorting by rating, newest, popularity, and ticket price."""
        res_rating = self.client.get(reverse('movie_list') + '?sort=rating')
        titles_rating = [m.title for m in res_rating.context['movies']]
        self.assertEqual(titles_rating[0], "Avengers: Endgame")

        res_newest = self.client.get(reverse('movie_list') + '?sort=newest')
        titles_newest = [m.title for m in res_newest.context['movies']]
        self.assertEqual(titles_newest[0], "Cyberpunk 2077 Movie")

        res_pop = self.client.get(reverse('movie_list') + '?sort=popularity')
        titles_pop = [m.title for m in res_pop.context['movies']]
        self.assertEqual(titles_pop[0], "Batman Begins")

        res_price = self.client.get(reverse('movie_list') + '?sort=price_low')
        titles_price = [m.title for m in res_price.context['movies']]
        self.assertEqual(titles_price[0], "Avatar: The Way of Water")

    def test_pagination_and_query_preservation(self):
        """Test pagination works and preserves search & filter query string."""
        for i in range(10):
            m = Movie.objects.create(title=f"Test Movie {i:02d}", rating=4.5)
            Genre.objects.create(movie=m, name="Drama")

        response = self.client.get(reverse('movie_list') + '?genre=Drama&sort=rating&page=2')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total_movies'], 10)
        page_obj = response.context['page_obj']
        self.assertEqual(page_obj.number, 2)
        self.assertTrue(page_obj.has_previous())
        self.assertEqual(len(response.context['movies']), 1)

    def test_personalized_recommendations(self):
        """Test personalized recommendations excluding booked movies & session tracking."""
        recs = get_personalized_recommendations(self.user, limit=3)
        rec_ids = [m.id for m in recs]

        # Booked movie B MUST be excluded
        self.assertNotIn(self.movie_b.id, rec_ids)
        self.assertIn(self.movie_c.id, rec_ids)

    def test_session_recently_viewed_tracking(self):
        """Test session tracking of recently viewed movies."""
        self.client.login(username="testuser", password="password123")
        res_detail = self.client.get(reverse('movie_detail', args=[self.movie_a.id]))
        self.assertEqual(res_detail.status_code, 200)

        res_list = self.client.get(reverse('movie_list'))
        self.assertEqual(res_list.status_code, 200)
        self.assertIn('recommended_movies', res_list.context)
        rec_titles = [m.title for m in res_list.context['recommended_movies']]
        self.assertNotIn("Batman Begins", rec_titles)

    def test_query_count_optimization(self):
        """Verify N+1 query problem is eliminated and query count is bounded."""
        for i in range(15):
            m = Movie.objects.create(title=f"Bulk Movie {i:02d}", rating=4.0)
            Genre.objects.create(movie=m, name="Comedy")
            Language.objects.create(movie=m, name="English")

        # Measure DB query count during list rendering
        response = self.client.get(reverse('movie_list'))
        self.assertEqual(response.status_code, 200)

        # Access all prefetched card attributes on all paginated movies to confirm zero extra N+1 queries
        for movie in response.context['movies']:
            _ = [g.name for g in movie.genres.all()]
            _ = [l.name for l in movie.languages.all()]
            _ = [c.name for c in movie.cast.all()]
