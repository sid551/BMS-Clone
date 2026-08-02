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
    class Meta:
        model = Movie
        fields = [
            'title', 'description', 'duration_minutes', 'release_date',
            'age_certification', 'trailer_url', 'status', 'poster',
            'genres', 'languages', 'cast'
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
            'genres': forms.SelectMultiple(attrs={'class': 'form-control', 'style': 'height: 120px;'}),
            'languages': forms.SelectMultiple(attrs={'class': 'form-control', 'style': 'height: 120px;'}),
            'cast': forms.SelectMultiple(attrs={'class': 'form-control', 'style': 'height: 120px;'}),
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
    show_time = forms.DateTimeField(
        widget=forms.DateTimeInput(
            attrs={'class': 'form-control', 'type': 'datetime-local'},
            format='%Y-%m-%dT%H:%M'
        ),
        input_formats=['%Y-%m-%dT%H:%M', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M']
    )

    class Meta:
        model = ShowSchedule
        fields = ['movie', 'theater', 'screen', 'show_time', 'price', 'available_seats']
        widgets = {
            'movie': forms.Select(attrs={'class': 'form-control'}),
            'theater': forms.Select(attrs={'class': 'form-control'}),
            'screen': forms.Select(attrs={'class': 'form-control'}),
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
        if self.instance and self.instance.pk and self.instance.show_time:
            self.initial['show_time'] = self.instance.show_time.strftime('%Y-%m-%dT%H:%M')


    def clean(self):
        cleaned_data = super().clean()
        screen = cleaned_data.get('screen')
        theater = cleaned_data.get('theater')
        available_seats = cleaned_data.get('available_seats')

        max_capacity = 0
        if screen:
            max_capacity = screen.total_seats
        elif theater:
            max_capacity = theater.total_seats

        if available_seats and max_capacity > 0 and available_seats > max_capacity:
            raise forms.ValidationError(
                f'Capacity Inconsistency Error: Available seats ({available_seats}) cannot exceed screen capacity ({max_capacity}).'
            )
        return cleaned_data



