"""Context for the German invoice print format ("Rechnung DE")."""

from collections import defaultdict

import frappe
from frappe.utils import flt, fmt_money, formatdate, strip_html

from de_billing.billing.accounts import effective_vat_rate, get_tax_category
from de_billing.billing.invoice import DOWN_PAYMENT, FINAL, kind_of, received_amount, vat_rate_of_account

LEGAL_NOTES = {
	"§ 13b UStG": "Steuerschuldnerschaft des Leistungsempfängers gemäß § 13b UStG.",
	"EU-Lieferung": "Steuerfreie innergemeinschaftliche Lieferung gemäß § 4 Nr. 1b UStG.",
	"EU-Leistung": "Steuerschuldnerschaft des Leistungsempfängers (Reverse Charge, Art. 196 MwStSystRL).",
}


def eur(value) -> str:
	return f"{fmt_money(flt(value, 2), precision=2)} €"


def german_number(value) -> str:
	"""1234.5 -> "1.234,5"; whole numbers without decimals."""
	text = f"{flt(value):,.3f}".rstrip("0").rstrip(".")
	return text.translate(str.maketrans(",.", ".,"))


def get_invoice_context(doc) -> dict:
	kind = kind_of(doc)
	category = get_tax_category(doc)
	customer = frappe.get_cached_doc("Customer", doc.customer)
	order = frappe.get_cached_doc("Sales Order", doc.de_sales_order) if doc.get("de_sales_order") else None

	service_rows = [row for row in doc.items if not row.get("de_deduction_for")]
	closing_rate = _closing_rate(doc)

	def rate_of(row) -> int:
		return closing_rate if closing_rate is not None else effective_vat_rate(doc, row)

	positions = []
	for idx, row in enumerate(service_rows, 1):
		description = row.description if row.description and row.description != row.item_name else ""
		positions.append(
			{
				"pos": idx,
				"title": row.item_name,
				"description": description,
				"qty": german_number(row.qty),
				"uom": row.uom,
				"rate": eur(row.rate),
				"vat": f"{rate_of(row)} %",
				"amount": eur(row.amount),
			}
		)

	net_services = flt(sum(flt(row.net_amount) for row in service_rows), 2)
	vat_lines = _vat_lines(doc, service_rows, rate_of)
	vat_services = flt(sum(line["vat_value"] for line in vat_lines), 2)

	deductions = [
		{
			"invoice": row.down_payment_invoice,
			"date": formatdate(row.posting_date),
			"net": eur(row.net_amount),
			"vat": eur(row.tax_amount),
			"gross": eur(row.paid_amount),
			"received": flt(row.paid_amount),
			"unpaid": flt(row.unpaid_amount),
			"closing_document": row.closing_document,
		}
		for row in doc.get("de_down_payment_deductions") or []
	]
	unpaid_closed = [d for d in deductions if d["unpaid"]]
	deductions = [d for d in deductions if d["received"]]

	notes = []
	if category in LEGAL_NOTES:
		notes.append(LEGAL_NOTES[category])
	if customer.get("de_customer_kind") == "Private Customer":
		notes.append(
			"Sie sind gesetzlich verpflichtet, diese Rechnung zwei Jahre aufzubewahren (§ 14b Abs. 1 Satz 5 UStG)."
		)
	if unpaid_closed:
		closed = ", ".join(f"{d['invoice']} ({eur(d['unpaid'])})" for d in unpaid_closed)
		notes.append(
			f"Nicht gezahlte Abschlagsbeträge werden nicht mehr gefordert und wurden ausgebucht: {closed}."
		)

	return {
		"title": _title(doc, kind),
		"intro": _intro(doc, kind, order),
		"meta": _meta(doc, customer, order),
		"recipient": _recipient(doc, customer),
		"positions": positions,
		"is_final": kind == FINAL and not doc.is_return,
		"net_services": eur(net_services),
		"vat_lines": vat_lines,
		"gross_services": eur(net_services + vat_services),
		"net_total": eur(doc.net_total),
		"grand_total": eur(doc.grand_total),
		"deductions": deductions,
		"prepaid": eur(doc.get("de_prepaid_amount")),
		"payment_note": _payment_note(doc),
		"notes": notes,
		"company": _company(doc.company),
	}


def _title(doc, kind) -> str:
	if doc.is_return:
		return "Rechnungskorrektur"
	if kind == DOWN_PAYMENT:
		return f"{doc.de_down_payment_no or ''}. Abschlagsrechnung".lstrip(". ")
	if kind == FINAL:
		return "Schlussrechnung"
	return "Rechnung"


def _intro(doc, kind, order) -> str:
	if doc.is_return:
		return f"wir korrigieren die Rechnung {doc.return_against} wie folgt:"
	if kind == DOWN_PAYMENT:
		return f"gemäß Auftrag {order.name if order else ''} berechnen wir Ihnen folgende Abschlagszahlung:"
	if kind == FINAL:
		return (
			f"wir berechnen Ihnen die erbrachten Leistungen zum Auftrag {order.name if order else ''} "
			"und verrechnen die erhaltenen Abschlagszahlungen:"
		)
	return "für die ausgeführten Leistungen berechnen wir Ihnen:"


def _meta(doc, customer, order) -> list[tuple[str, str]]:
	meta = [
		("Rechnungsnummer" if not doc.is_return else "Korrekturnummer", doc.name),
		("Rechnungsdatum", formatdate(doc.posting_date)),
		("Leistungsdatum", doc.get("de_service_period") or formatdate(doc.posting_date)),
	]
	if doc.is_return and doc.return_against:
		meta.append(("Zu Rechnung", doc.return_against))
	if order:
		meta.append(("Auftrag", order.name))
		if order.po_no:
			meta.append(("Ihre Bestellung", order.po_no))
	if customer.get("leitweg_id"):
		meta.append(("Leitweg-ID", customer.leitweg_id))
	if customer.tax_id:
		meta.append(("Ihre USt-IdNr.", customer.tax_id))
	return meta


def _recipient(doc, customer) -> list[str]:
	lines = [doc.customer_name]
	if doc.customer_address:
		address = frappe.get_cached_doc("Address", doc.customer_address)
		lines += [
			address.address_line1,
			address.address_line2,
			f"{address.pincode or ''} {address.city or ''}".strip(),
		]
		if address.country and address.country != "Germany":
			lines.append(
				frappe.get_cached_value("Country", address.country, "country_name") or address.country
			)
	return [line for line in lines if line]


def _closing_rate(doc) -> int | None:
	"""Correction invoices that close a down payment reverse its VAT through an explicit tax row
	(their item row carries 0 % item tax). Print them with the VAT rate of that down payment."""
	if not (doc.is_return and doc.get("de_closing_for")):
		return None
	rates = {vat_rate_of_account(doc.company, tax.account_head) for tax in doc.taxes if flt(tax.tax_amount)}
	return rates.pop() if len(rates) == 1 else (0 if not rates else None)


def _vat_lines(doc, service_rows, rate_of) -> list[dict]:
	base_by_rate = defaultdict(float)
	for row in service_rows:
		base_by_rate[rate_of(row)] += flt(row.net_amount)

	vat_by_rate = defaultdict(float)
	for tax in doc.taxes:
		if tax.get("de_deduction_row"):
			continue
		vat_by_rate[vat_rate_of_account(doc.company, tax.account_head)] += flt(
			tax.tax_amount_after_discount_amount
		)

	lines = []
	for rate in sorted(base_by_rate, reverse=True):
		vat = flt(vat_by_rate.get(rate, 0), 2)
		if rate == 0 and not vat:
			continue
		lines.append(
			{"label": f"zzgl. {rate} % USt. auf {eur(base_by_rate[rate])}", "vat": eur(vat), "vat_value": vat}
		)
	return lines


def _payment_note(doc) -> str | None:
	if doc.is_return or flt(doc.grand_total) <= 0:
		return None
	if doc.docstatus == 1 and flt(doc.outstanding_amount) <= 0:
		if flt(received_amount(doc.name), 2) >= flt(doc.grand_total, 2):
			return "Der Rechnungsbetrag ist bereits beglichen. Vielen Dank."
		final_invoice = frappe.db.get_value(
			"Sales Invoice",
			{"return_against": doc.name, "docstatus": 1, "de_closing_for": ("is", "set")},
			"de_closing_for",
		)
		if final_invoice:
			return (
				f"Diese Abschlagsrechnung ist mit der Schlussrechnung {final_invoice} abgerechnet; "
				"der nicht gezahlte Betrag wurde ausgebucht."
			)
		return None
	return (
		f"Bitte überweisen Sie {eur(doc.outstanding_amount or doc.grand_total)} bis zum "
		f"{formatdate(doc.due_date)} ohne Abzug unter Angabe der Rechnungsnummer {doc.name}."
	)


def _company(company: str) -> dict:
	doc = frappe.get_cached_doc("Company", company)
	address_name = frappe.db.get_value(
		"Dynamic Link",
		{"link_doctype": "Company", "link_name": company, "parenttype": "Address"},
		"parent",
	)
	address = frappe.get_cached_doc("Address", address_name) if address_name else None
	bank = frappe.db.get_value(
		"Bank Account",
		{"company": company, "is_company_account": 1, "is_default": 1},
		["bank", "iban", "branch_code"],
		as_dict=True,
	) or frappe.db.get_value(
		"Bank Account",
		{"company": company, "is_company_account": 1},
		["bank", "iban", "branch_code"],
		as_dict=True,
	)
	if bank and bank.iban:
		iban = bank.iban.replace(" ", "")
		bank.iban_display = " ".join(iban[i : i + 4] for i in range(0, len(iban), 4))
	street = address.address_line1 if address else ""
	city = f"{address.pincode or ''} {address.city or ''}".strip() if address else ""
	return {
		"name": doc.company_name,
		"tagline": strip_html(doc.get("company_description") or "").strip().splitlines()[0]
		if doc.get("company_description")
		else "",
		"street": street,
		"city": city,
		"sender_line": " · ".join(part for part in (doc.company_name, street, city) if part),
		"phone": doc.phone_no,
		"email": doc.email,
		"website": doc.website,
		"tax_id": doc.tax_id,
		"registration": [
			line.strip() for line in (doc.registration_details or "").splitlines() if line.strip()
		],
		"bank": bank,
	}
