from django import forms
from .models import Movie, Genre, Language, CastMember, Theater, Screen, ShowSchedule, Review, ReportedReview


class ReviewForm(forms.ModelForm):
    class Meta:
        model = Review
        fields = ['rating', 'title', 'text']
        widgets = {
            'rating': forms.Select(attrs={'class': 'form-control'}),
            'title': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Review title'}),
            'text': forms.Textarea(attrs={'class': 'form-control', 'rows': 4, 'placeholder': 'Write your review...'}),
        }


class ReportReviewForm(forms.ModelForm):
    class Meta:
        model = ReportedReview
        fields = ['reason', 'comments']
        widgets = {
            'reason': forms.Select(attrs={'class': 'form-control'}),
            'comments': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': 'Optional: provide additional context...'
            }),
        }


class MovieForm(forms.ModelForm):
    genre_names = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Action, Sci-Fi, Drama (comma separated)'}),
        help_text="Separate genres with commas for this specific movie."
    )
    language_names = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. English, Hindi, Tamil (comma separated)'}),
        help_text="Separate languages with commas for this specific movie."
    )
    cast_names = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Cillian Murphy, Emily Blunt, Robert Downey Jr.'}),
        help_text="Separate actor names with commas for this specific movie."
    )

    class Meta:
        model = Movie
        fields = [
            'title', 'description', 'duration_minutes', 'release_date',
            'age_certification', 'trailer_url', 'status', 'poster'
        ]
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Movie title'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 4}),
            'duration_minutes': forms.NumberInput(attrs={'class': 'form-control', 'min': 0}),
            'release_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'age_certification': forms.Select(attrs={'class': 'form-control'}),
            'trailer_url': forms.URLInput(attrs={'class': 'form-control', 'placeholder': 'https://www.youtube.com/watch?v=...'}),
            'status': forms.Select(attrs={'class': 'form-control'}),
            'poster': forms.FileInput(attrs={'class': 'form-control-file'}),
        }



class GenreForm(forms.ModelForm):
    class Meta:
        model = Genre
        fields = ['name']
        widgets = {'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Genre name'})}


class LanguageForm(forms.ModelForm):
    class Meta:
        model = Language
        fields = ['name']
        widgets = {'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Language name'})}


class CastMemberForm(forms.ModelForm):
    class Meta:
        model = CastMember
        fields = ['name', 'role', 'photo']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'role': forms.Select(attrs={'class': 'form-control'}),
            'photo': forms.FileInput(attrs={'class': 'form-control-file'}),
        }


class TheaterForm(forms.ModelForm):
    class Meta:
        model = Theater
        fields = ['name', 'location', 'total_seats']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'location': forms.TextInput(attrs={'class': 'form-control'}),
            'total_seats': forms.NumberInput(attrs={'class': 'form-control', 'min': 1}),
        }


class ScreenForm(forms.ModelForm):
    class Meta:
        model = Screen
        fields = ['theater', 'name', 'screen_type', 'total_rows', 'seats_per_row']
        widgets = {
            'theater': forms.Select(attrs={'class': 'form-control'}),
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Screen 1 - IMAX'}),
            'screen_type': forms.Select(attrs={'class': 'form-control'}),
            'total_rows': forms.NumberInput(attrs={'class': 'form-control', 'min': 1, 'max': 26}),
            'seats_per_row': forms.NumberInput(attrs={'class': 'form-control', 'min': 1, 'max': 30}),
        }


class ShowScheduleForm(forms.ModelForm):
    class Meta:
        model = ShowSchedule
        fields = ['movie', 'theater', 'screen', 'show_time', 'price', 'available_seats']
        widgets = {
            'movie': forms.Select(attrs={'class': 'form-control'}),
            'theater': forms.Select(attrs={'class': 'form-control'}),
            'screen': forms.Select(attrs={'class': 'form-control'}),
            'show_time': forms.DateTimeInput(
                attrs={'class': 'form-control', 'type': 'datetime-local'},
                format='%Y-%m-%dT%H:%M'
            ),
            'price': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'available_seats': forms.NumberInput(attrs={
                'class': 'form-control',
                'min': 0,
                'placeholder': 'Auto-synced with Screen Capacity if blank'
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['available_seats'].required = False
        self.fields['show_time'].input_formats = [
            '%Y-%m-%dT%H:%M', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M'
        ]
        if self.instance and self.instance.pk and self.instance.show_time:
            self.initial['show_time'] = self.instance.show_time.strftime('%Y-%m-%dT%H:%M')

        # Dynamically restrict screens to the selected theater if available
        theater_id = None
        if 'theater' in self.data:
            try:
                theater_id = int(self.data.get('theater'))
            except (ValueError, TypeError):
                pass
        elif self.instance and self.instance.pk and self.instance.theater_id:
            theater_id = self.instance.theater_id

        if theater_id:
            self.fields['screen'].queryset = Screen.objects.filter(theater_id=theater_id)

    def clean_available_seats(self):
        seats = self.cleaned_data.get('available_seats')
        if seats is None or seats == '':
            return 0
        return seats

    def clean(self):
        cleaned_data = super().clean()
        screen = cleaned_data.get('screen')
        theater = cleaned_data.get('theater')
        available_seats = cleaned_data.get('available_seats')

        if screen and not theater:
            cleaned_data['theater'] = screen.theater
            theater = screen.theater

        if theater and screen and screen.theater != theater:
            self.add_error('screen', f'Screen "{screen}" does not belong to selected theater "{theater.name}".')

        max_capacity = 0
        if screen:
            max_capacity = screen.total_seats
        elif theater:
            max_capacity = theater.total_seats

        if not available_seats or available_seats == 0:
            cleaned_data['available_seats'] = max_capacity
        elif max_capacity > 0 and available_seats > max_capacity:
            cleaned_data['available_seats'] = max_capacity

        return cleaned_data






