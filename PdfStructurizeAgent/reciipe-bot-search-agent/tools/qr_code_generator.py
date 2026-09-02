from strands import tool
import qrcode
import io

@tool
def generate_qr_code(data: str) -> bytes:
    """Generate a QR code from the provided data.

    Args:
        data: The data to encode in the QR code.

    Returns:
        The QR code image in bytes.
    """
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(data)
    qr.make(fit=True)

    img = qr.make_image(fill='black', back_color='white')
    img_bytes = io.BytesIO()
    img.save(img_bytes, format='PNG')
    return img_bytes.getvalue()
