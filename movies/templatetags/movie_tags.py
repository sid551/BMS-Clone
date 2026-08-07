import re
from django import template
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter
def image_url(image_field, fallback='https://placehold.co/300x400?text=No+Image'):
    """Return the image URL safely, fallback if no file attached."""
    try:
        return image_field.url if image_field and image_field.name else fallback
    except Exception:
        return fallback


@register.filter
def youtube_embed_id(url):
    """
    Extract the YouTube video ID and return a standard embed URL.
    Supports youtube.com/watch?v=ID, youtu.be/ID, shorts, embed, m.youtube, music.youtube, etc.
    """
    if not url:
        return ''
    patterns = [
        r'(?:v=|\/embed\/|\/shorts\/|youtu\.be\/|\/v\/|\/e\/)([a-zA-Z0-9_-]{11})',
        r'[\?&]v=([a-zA-Z0-9_-]{11})',
    ]
    for pattern in patterns:
        match = re.search(pattern, str(url))
        if match:
            video_id = match.group(1)
            return f'https://www.youtube.com/embed/{video_id}?rel=0&modestbranding=1'
    return ''


@register.filter
def format_duration(minutes):
    """Format duration in minutes as Xh Ym or Ym."""
    try:
        mins = int(minutes)
        if mins <= 0:
            return ''
        hours = mins // 60
        remaining_mins = mins % 60
        if hours > 0 and remaining_mins > 0:
            return f'{hours}h {remaining_mins}m'
        elif hours > 0:
            return f'{hours}h'
        else:
            return f'{remaining_mins}m'
    except (ValueError, TypeError):
        return ''


@register.filter
def star_range(rating):
    """Return a list to iterate for star rendering."""
    try:
        return range(1, 6)
    except Exception:
        return range(1, 6)


@register.simple_tag
def render_stars(rating):
    """Render filled/empty stars as HTML."""
    stars = ''
    for i in range(1, 6):
        if i <= rating:
            stars += '<span class="text-warning">&#9733;</span>'
        else:
            stars += '<span class="text-muted">&#9734;</span>'
    return mark_safe(stars)


@register.filter
def get_item(dictionary, key):
    """Safely get value from dictionary by key in template."""
    if isinstance(dictionary, dict):
        return dictionary.get(key)
    return None


@register.simple_tag(takes_context=True)
def url_replace(context, **kwargs):
    """Safely update GET query string parameters while preserving existing ones."""
    query = context['request'].GET.copy()
    for key, value in kwargs.items():
        if value is not None and value != '':
            query[key] = value
        else:
            query.pop(key, None)
    return query.urlencode()



