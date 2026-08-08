import io
import json
import logging
from PIL import Image as PILImage
import qrcode
from django.core.files.base import ContentFile
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, HRFlowable
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

logger = logging.getLogger(__name__)


def generate_qr_code_image(data_str):
    """
    Generate an in-memory QR code image as bytes.
    """
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=6,
        border=2,
    )
    qr.add_data(data_str)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#1F2937", back_color="#FFFFFF")
    
    buffer = io.BytesIO()
    img.save(buffer)
    buffer.seek(0)

    return buffer


def generate_ticket_pdf(booking):
    """
    Generate a professional cinema PDF ticket for a confirmed Booking.
    Returns a ContentFile object containing the PDF bytes.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36
    )

    styles = getSampleStyleSheet()

    # Custom styles
    title_style = ParagraphStyle(
        'HeaderTitle',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=20,
        textColor=colors.HexColor('#FFFFFF'),
        alignment=0,
        spaceAfter=2
    )
    subtitle_style = ParagraphStyle(
        'HeaderSubtitle',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=10,
        textColor=colors.HexColor('#F3F4F6'),
        alignment=0
    )
    movie_title_style = ParagraphStyle(
        'MovieTitle',
        parent=styles['Heading2'],
        fontName='Helvetica-Bold',
        fontSize=18,
        textColor=colors.HexColor('#111827'),
        spaceAfter=4
    )
    label_style = ParagraphStyle(
        'MetaLabel',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=9,
        textColor=colors.HexColor('#4B5563')
    )
    value_style = ParagraphStyle(
        'MetaValue',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=10,
        textColor=colors.HexColor('#111827')
    )
    price_style = ParagraphStyle(
        'PriceTag',
        parent=styles['Heading2'],
        fontName='Helvetica-Bold',
        fontSize=15,
        textColor=colors.HexColor('#E11D48')
    )
    small_note_style = ParagraphStyle(
        'SmallNote',
        parent=styles['Normal'],
        fontName='Helvetica-Oblique',
        fontSize=8,
        textColor=colors.HexColor('#6B7280'),
        alignment=1
    )

    elements = []

    # 1. Header Banner
    header_data = [
        [
            Paragraph("<b>MOVIE TICKET</b>", title_style),
            Paragraph(f"Ref: <b>{booking.booking_reference}</b>", ParagraphStyle('RefHeader', parent=subtitle_style, alignment=2))
        ]
    ]
    header_table = Table(header_data, colWidths=[340, 200])
    header_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#E11D48')),
        ('PADDING', (0, 0), (-1, -1), 10),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
    ]))
    elements.append(header_table)
    elements.append(Spacer(1, 15))

    # Fetch booking details
    movie = booking.movie or (booking.show_schedule.movie if booking.show_schedule else None)
    theater = booking.theater or (booking.show_schedule.theater if booking.show_schedule else None)
    screen = booking.show_schedule.screen if (booking.show_schedule and booking.show_schedule.screen) else None
    
    movie_title = movie.title if movie else "Movie Ticket"
    duration_str = f" | {movie.duration_formatted}" if (movie and movie.duration_formatted) else ""
    rating_str = f" | {movie.age_certification}" if (movie and movie.age_certification) else ""
    
    theater_name = theater.name if theater else "Theater"
    screen_name = screen.name if screen else "Screen 1"
    
    if booking.show_schedule and booking.show_schedule.show_time:
        show_time_dt = booking.show_schedule.show_time
        show_date_str = show_time_dt.strftime("%d %b %Y")
        show_clock_str = show_time_dt.strftime("%I:%M %p")
    else:
        show_date_str = booking.booked_at.strftime("%d %b %Y")
        show_clock_str = booking.booked_at.strftime("%I:%M %p")

    # Fetch booked seat numbers
    booked_seats_qs = booking.booked_seats.select_related('seat').all()
    if booked_seats_qs.exists():
        seat_numbers = ", ".join([bs.seat.seat_number for bs in booked_seats_qs])
    elif booking.seat:
        seat_numbers = booking.seat.seat_number
    else:
        seat_numbers = f"{booking.number_of_seats} Seat(s)"

    # Fetch payment reference if available
    payment_tx_id = "N/A"
    try:
        from .models import Payment
        payment = Payment.objects.filter(booking=booking, status='success').first()
        if payment and payment.gateway_payment_id:
            payment_tx_id = payment.gateway_payment_id
        elif payment and payment.gateway_order_id:
            payment_tx_id = payment.gateway_order_id
    except Exception:
        pass

    # 2. Main Content Grid (Poster on Left if available, Details on Right)
    poster_element = None
    if movie and (movie.poster or movie.image):
        image_field = movie.poster or movie.image
        img_bytes = None

        # Attempt 1: Local file path
        if hasattr(image_field, 'path'):
            try:
                if image_field.storage.exists(image_field.name):
                    with open(image_field.path, 'rb') as f:
                        img_bytes = f.read()
            except Exception:
                img_bytes = None

        # Attempt 2: Storage open
        if not img_bytes:
            try:
                image_field.open('rb')
                img_bytes = image_field.read()
                image_field.close()
            except Exception:
                img_bytes = None

        # Attempt 3: Remote HTTP fetch for Cloudinary or external image URLs
        if not img_bytes and hasattr(image_field, 'url') and image_field.url:
            try:
                import urllib.request
                url = image_field.url
                if url.startswith('//'):
                    url = 'https:' + url
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    if resp.status == 200:
                        img_bytes = resp.read()
            except Exception as e:
                logger.warning(f"Could not fetch movie poster URL '{image_field.url}' via HTTP: {e}")
                img_bytes = None

        # Convert image bytes to PIL Image for ReportLab
        if img_bytes:
            try:
                p_buf_in = io.BytesIO(img_bytes)
                with PILImage.open(p_buf_in) as pil_img:
                    pil_img = pil_img.convert('RGB')
                    p_buf_out = io.BytesIO()
                    pil_img.save(p_buf_out, format='JPEG', quality=85)
                    p_buf_out.seek(0)
                    poster_element = Image(p_buf_out, width=110, height=155)
            except Exception as e:
                logger.warning(f"Could not process poster image bytes for PDF: {e}")
                poster_element = None

    if not poster_element:
        # Placeholder poster box
        placeholder_table = Table([[Paragraph("<b>BOOKMYSHOW<br/>CINEMA</b>", small_note_style)]], colWidths=[110], rowHeights=[155])
        placeholder_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#F3F4F6')),
            ('BOX', (0,0), (-1,-1), 1, colors.HexColor('#E5E7EB')),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ]))
        poster_element = placeholder_table

    # Generate QR Code representing secure ticket verification identifier
    verify_url = f"/booking/{booking.booking_reference}/verify/"
    qr_buf = generate_qr_code_image(verify_url)
    qr_element = Image(qr_buf, width=110, height=110)

    # Details table layout matching spec
    details_data = [
        [Paragraph(f"<b>Movie:</b> {movie_title}", movie_title_style), ""],
        [Paragraph(f"<font color='#6B7280'>{duration_str}{rating_str}</font>", value_style), ""],
        [Paragraph("Theater:", label_style), Paragraph(f"<b>{theater_name}</b>", value_style)],
        [Paragraph("Screen:", label_style), Paragraph(f"<b>{screen_name}</b>", value_style)],
        [Paragraph("Date:", label_style), Paragraph(f"<b>{show_date_str}</b>", value_style)],
        [Paragraph("Time:", label_style), Paragraph(f"<b>{show_clock_str}</b>", value_style)],
        [Paragraph("Seats:", label_style), Paragraph(f"<b>{seat_numbers}</b>", value_style)],
        [Paragraph("Booking ID:", label_style), Paragraph(f"<b>{booking.booking_reference}</b>", value_style)],
        [Paragraph("Payment Ref:", label_style), Paragraph(f"<code>{payment_tx_id}</code>", value_style)],
        [Paragraph("Total Amount:", label_style), Paragraph(f"<b>₹{booking.total_price:,.2f}</b>", price_style)],
    ]

    details_table = Table(details_data, colWidths=[110, 290])
    details_table.setStyle(TableStyle([
        ('SPAN', (0, 0), (1, 0)),
        ('SPAN', (0, 1), (1, 1)),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('PADDING', (0, 0), (-1, -1), 2),
    ]))

    body_table = Table([
        [poster_element, details_table]
    ], colWidths=[120, 420])
    body_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('ALIGN', (0, 0), (0, 0), 'CENTER'),
    ]))
    elements.append(body_table)
    elements.append(Spacer(1, 10))

    # Perforation / Separator Line
    elements.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#D1D5DB'), spaceAfter=10, spaceBefore=5, hAlign='CENTER', vAlign='BOTTOM', dash=[4, 4]))

    # Footer Card with Verification QR Code & Entry Rules
    qr_box_data = [
        [
            qr_element,
            Paragraph(
                "<b>SCAN AT CINEMA ENTRY</b><br/><br/>"
                "• Please present this PDF ticket at the cinema entry.<br/>"
                "• Valid photo ID may be requested along with ticket.<br/>"
                "• Outside food & beverages are strictly prohibited.<br/>"
                f"• Booking Reference: <b>{booking.booking_reference}</b><br/>"
                f"• Account: <b>{booking.user.get_full_name() or booking.user.username}</b>",
                ParagraphStyle('EntryRules', parent=styles['Normal'], fontName='Helvetica', fontSize=8.5, leading=12, textColor=colors.HexColor('#374151'))
            )
        ]
    ]
    qr_table = Table(qr_box_data, colWidths=[125, 415])
    qr_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#F9FAFB')),
        ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#E5E7EB')),
        ('PADDING', (0, 0), (-1, -1), 8),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (0, 0), (0, 0), 'CENTER'),
    ]))
    elements.append(qr_table)
    elements.append(Spacer(1, 10))

    elements.append(Paragraph("Thank you for booking!", small_note_style))

    # Build PDF document
    doc.build(elements)
    buffer.seek(0)
    return ContentFile(buffer.getvalue(), name=f"ticket_{booking.booking_reference}.pdf")


def generate_and_save_ticket(booking):
    """
    Generate ticket PDF for confirmed booking and save it to booking.ticket.
    Catches all exceptions to ensure already-confirmed booking status is never affected.
    """
    if booking.status != 'confirmed':
        logger.info(f"Skipping ticket generation for booking {booking.id} with status '{booking.status}'")
        return False

    try:
        pdf_file = generate_ticket_pdf(booking)
        filename = f"ticket_{booking.booking_reference}.pdf"
        booking.ticket.save(filename, pdf_file, save=False)
        # Update ticket field directly in DB to avoid triggering signals again
        type(booking).objects.filter(pk=booking.pk).update(ticket=booking.ticket.name)
        logger.info(f"Successfully generated and saved PDF ticket for booking {booking.booking_reference}")
        return True
    except Exception as e:
        logger.error(
            f"Failed to generate PDF ticket for booking {booking.booking_reference} (ID: {booking.pk}): {str(e)}",
            exc_info=True
        )
        return False


def get_booking_ticket_bytes(booking):
    """
    Retrieve raw PDF bytes for a confirmed booking ticket.
    Attempts:
    1. Read directly from booking.ticket if stored locally/remotely.
    2. HTTP fetch from booking.ticket.url if direct file read fails on remote storage (Cloudinary).
    3. Re-generate PDF on-the-fly via ReportLab generate_ticket_pdf(booking) if missing or unreadable.

    Ensures PDF bytes are ALWAYS available for downloads and email attachments.
    """
    pdf_bytes = None

    # Attempt 1: Read directly from booking.ticket FileField
    if booking.ticket and booking.ticket.name:
        try:
            booking.ticket.open('rb')
            pdf_bytes = booking.ticket.read()
            booking.ticket.close()
        except Exception as e:
            logger.warning(f"Could not read booking.ticket file directly for {booking.booking_reference}: {e}")
            pdf_bytes = None

        # Attempt 2: If direct read failed (e.g. Cloudinary remote storage), fetch via HTTP from ticket.url
        if not pdf_bytes and hasattr(booking.ticket, 'url'):
            try:
                import urllib.request
                req = urllib.request.Request(
                    booking.ticket.url,
                    headers={'User-Agent': 'Mozilla/5.0'}
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    if resp.status == 200:
                        pdf_bytes = resp.read()
            except Exception as e:
                logger.warning(f"Could not fetch ticket URL '{booking.ticket.url}' via HTTP for {booking.booking_reference}: {e}")
                pdf_bytes = None

    # Attempt 3: On-the-fly generation if stored file is missing or unreadable
    if not pdf_bytes:
        try:
            content_file = generate_ticket_pdf(booking)
            pdf_bytes = content_file.read()
            # Attempt background save if missing
            if not booking.ticket or not booking.ticket.name:
                generate_and_save_ticket(booking)
        except Exception as e:
            logger.error(f"Failed to generate ticket PDF on-the-fly for {booking.booking_reference}: {e}", exc_info=True)

    return pdf_bytes

