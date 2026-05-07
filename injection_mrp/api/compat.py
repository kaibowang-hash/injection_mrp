from __future__ import annotations

from html import escape
from io import BytesIO

import frappe
from frappe import _
from frappe.utils import cstr


MAX_BARCODE_VALUE_LENGTH = 128


@frappe.whitelist(allow_guest=True)
def make_barcode(value=None, barcode=None, data=None, text=None, code=None, barcode_type=None, format=None, **kwargs):
	"""V15 compatibility endpoint for legacy /api/method/frappe.utils.make_barcode image URLs."""
	barcode_value = cstr(value or barcode or data or text or code or kwargs.get("name") or "").strip()
	if not barcode_value:
		frappe.throw(_("Barcode value is required."))
	if len(barcode_value) > MAX_BARCODE_VALUE_LENGTH:
		frappe.throw(_("Barcode value is too long."))

	requested_format = cstr(barcode_type or format or kwargs.get("type") or "code128").strip().lower()
	if requested_format in {"qr", "qrcode", "qr_code"}:
		svg = _make_qr_svg(barcode_value)
	else:
		svg = _make_linear_barcode_svg(barcode_value, requested_format)

	frappe.local.response.filename = "barcode.svg"
	frappe.local.response.filecontent = svg.encode("utf-8")
	frappe.local.response.type = "download"
	frappe.local.response.content_type = "image/svg+xml; charset=utf-8"
	frappe.local.response.display_content_as = "inline"


def _make_linear_barcode_svg(value: str, requested_format: str) -> str:
	try:
		from barcode import get_barcode_class
		from barcode.writer import SVGWriter

		barcode_class = get_barcode_class(_normalize_barcode_format(requested_format))
		buffer = BytesIO()
		barcode_class(value, writer=SVGWriter()).write(
			buffer,
			options={
				"write_text": False,
				"module_width": 0.28,
				"module_height": 18,
				"quiet_zone": 2,
			},
		)
		return buffer.getvalue().decode("utf-8")
	except Exception:
		return _make_text_svg(value)


def _normalize_barcode_format(requested_format: str) -> str:
	aliases = {
		"": "code128",
		"code-128": "code128",
		"code_128": "code128",
		"ean": "ean13",
	}
	return aliases.get(requested_format, requested_format)


def _make_qr_svg(value: str) -> str:
	try:
		import qrcode
		import qrcode.image.svg

		image = qrcode.make(value, image_factory=qrcode.image.svg.SvgPathImage)
		buffer = BytesIO()
		image.save(buffer)
		return buffer.getvalue().decode("utf-8")
	except Exception:
		return _make_text_svg(value)


def _make_text_svg(value: str) -> str:
	safe_value = escape(value)
	return (
		'<svg xmlns="http://www.w3.org/2000/svg" width="320" height="80" viewBox="0 0 320 80">'
		'<rect width="320" height="80" fill="white"/>'
		'<text x="12" y="45" font-family="monospace" font-size="18" fill="black">'
		f"{safe_value}"
		"</text></svg>"
	)
