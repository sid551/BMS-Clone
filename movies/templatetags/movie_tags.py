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
    Supports youtube.com/watch?v=ID, youtu.be/ID, and youtube.com/shorts/ID.
    """
    if not url:
        return ''
    patterns = [
        r'(?:youtube\.com/watch\?v=)([a-zA-Z0-9_-]{11})',
        r'(?:youtu\.be/)([a-zA-Z0-9_-]{11})',
        r'(?:youtube\.com/shorts/)([a-zA-Z0-9_-]{11})',
        r'(?:youtube\.com/embed/)([a-zA-Z0-9_-]{11})',
    ]
    for pattern in patterns:
        match = re.search(pattern, str(url))
        if match:
            video_id = match.group(1)
            return f'https://www.youtube.com/embed/{video_id}?rel=0&modestbranding=1'
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
