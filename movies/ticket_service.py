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
            Paragraph("<b>BookMyShow</b> Cinema Pass", title_style),
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
    theater_loc = f" ({theater.location})" if (theater and theater.location) else ""
    screen_name = screen.name if screen else "Screen 1"
    
    if booking.show_schedule and booking.show_schedule.show_time:
        show_time_dt = booking.show_schedule.show_time
        show_time_str = show_time_dt.strftime("%A, %d %b %Y at %I:%M %p")
    else:
        show_time_str = booking.booked_at.strftime("%A, %d %b %Y at %I:%M %p")

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
        try:
            image_field = movie.poster or movie.image
            if hasattr(image_field, 'path') and image_field.storage.exists(image_field.name):
                img_path = image_field.path
                with PILImage.open(img_path) as pil_img:
                    pil_img = pil_img.convert('RGB')
                    p_buf = io.BytesIO()
                    pil_img.save(p_buf, format='JPEG', quality=85)
                    p_buf.seek(0)
                    poster_element = Image(p_buf, width=110, height=155)
            elif hasattr(image_field, 'file'):
                with PILImage.open(image_field.file) as pil_img:
                    pil_img = pil_img.convert('RGB')
                    p_buf = io.BytesIO()
                    pil_img.save(p_buf, format='JPEG', quality=85)
                    p_buf.seek(0)
                    poster_element = Image(p_buf, width=110, height=155)
        except Exception as e:
            logger.warning(f"Could not load poster for ticket PDF: {e}")
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


    # Details table layout
    details_data = [
        [Paragraph(f"<b>{movie_title}</b>", movie_title_style), ""],
        [Paragraph(f"<font color='#6B7280'>{duration_str}{rating_str}</font>", value_style), ""],
        [Spacer(1, 4), ""],
        [Paragraph("Theater & Screen", label_style), Paragraph(f"<b>{theater_name}</b>{theater_loc}<br/><font color='#4B5563'>{screen_name}</font>", value_style)],
        [Paragraph("Show Date & Time", label_style), Paragraph(f"<b>{show_time_str}</b>", value_style)],
        [Paragraph("Booked Seats", label_style), Paragraph(f"<b>{seat_numbers}</b> ({booking.number_of_seats} seat{'s' if booking.number_of_seats > 1 else ''})", value_style)],
        [Paragraph("Payment Ref / Tx ID", label_style), Paragraph(f"<code>{payment_tx_id}</code>", value_style)],
        [Paragraph("Total Amount", label_style), Paragraph(f"<b>₹{booking.total_price:,.2f}</b>", price_style)],
    ]

    details_table = Table(details_data, colWidths=[130, 270])
    details_table.setStyle(TableStyle([
        ('SPAN', (0, 0), (1, 0)),
        ('SPAN', (0, 1), (1, 1)),
        ('SPAN', (0, 2), (1, 2)),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('PADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 3), (-1, -2), 4),
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

    elements.append(Paragraph("Thank you for booking with BookMyShow. Enjoy your movie!", small_note_style))

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
