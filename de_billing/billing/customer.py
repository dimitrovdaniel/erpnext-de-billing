import re

import frappe
from frappe import _

# Leitweg-ID: coarse address (2-12 digits), optional fine address, two-digit check number.
LEITWEG_ID = re.compile(r"^\d{2,12}(-[0-9A-Z]{1,30})?-\d{2}$")


def validate(doc, method=None):
	if doc.get("de_customer_kind") != "Public Authority":
		return
	leitweg_id = (doc.get("leitweg_id") or "").strip().upper()
	if not leitweg_id:
		frappe.throw(
			_("Public-sector customers need a Leitweg-ID for XRechnung."),
			frappe.MandatoryError,
			title=_("Leitweg-ID missing"),
		)
	if not LEITWEG_ID.match(leitweg_id):
		frappe.throw(_("{0} is not a valid Leitweg-ID (e.g. 04011000-1234512345-06).").format(leitweg_id))
	doc.leitweg_id = leitweg_id
