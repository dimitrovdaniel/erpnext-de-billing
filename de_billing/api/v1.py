"""REST API for the office app (Supabase / React).

Every endpoint is ``/api/method/de_billing.api.v1.<function>``, authenticated with an ERPNext API
key of a dedicated integration user: ``Authorization: token <api_key>:<api_secret>``.
Customers, items and orders are referenced by their Supabase ID. See ``docs/openapi.yaml``.
"""

import base64

import frappe
from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
from erpnext.controllers.accounts_controller import get_taxes_and_charges
from frappe import _
from frappe.utils import add_days, flt, getdate, nowdate
from frappe.utils.nestedset import get_root_of

from de_billing.billing.invoice import DOWN_PAYMENT, FINAL, STANDARD
from de_billing.setup.company import item_tax_template_name, sales_tax_template_name
from de_billing.setup.constants import CUSTOMER_GROUPS, DOWN_PAYMENT_ITEM, TAX_TREATMENTS
from de_billing.webhooks.payloads import invoice_summary, payment_summary

PRINT_FORMAT = "Rechnung DE"
INVOICE_KINDS = {"standard": STANDARD, "down_payment": DOWN_PAYMENT, "final": FINAL}
CUSTOMER_KINDS = {
	"business": "Business Customer",
	"private": "Private Customer",
	"public_authority": "Public Authority",
}
VAT_RATES = (19, 7, 0)


# Customers and items
# -------------------


@frappe.whitelist(methods=["POST"])
def upsert_customer(
	supabase_id: str,
	customer_name: str,
	kind: str = "business",
	tax_treatment: str = "domestic",
	email: str | None = None,
	tax_id: str | None = None,
	leitweg_id: str | None = None,
	address: dict | None = None,
):
	"""Create or update the ERPNext copy of a customer. Safe to call repeatedly."""
	_require(supabase_id, "supabase_id")
	_require(customer_name, "customer_name")
	customer_kind = _choice(CUSTOMER_KINDS, kind, "kind")
	_choice(TAX_TREATMENTS, tax_treatment, "tax_treatment")

	name = _find("Customer", supabase_id)
	doc = frappe.get_doc("Customer", name) if name else frappe.new_doc("Customer")
	doc.update(
		{
			"supabase_id": supabase_id,
			"customer_name": customer_name,
			"customer_type": "Individual" if customer_kind == "Private Customer" else "Company",
			"customer_group": CUSTOMER_GROUPS[customer_kind],
			"de_customer_kind": customer_kind,
			"leitweg_id": leitweg_id,
			"tax_id": tax_id,
			"tax_category": TAX_TREATMENTS[tax_treatment][0],
		}
	)
	if not name:
		doc.territory = frappe.db.exists("Territory", "Germany") or get_root_of("Territory")
	doc.save()

	if address:
		_upsert_address(doc, frappe.parse_json(address))
	if email:
		_upsert_contact(doc, email)
	return {"name": doc.name, "created": not name}


@frappe.whitelist(methods=["POST"])
def upsert_item(
	supabase_id: str,
	item_name: str,
	rate: float | None = None,
	vat_rate: int = 19,
	uom: str = "Stk",
	item_code: str | None = None,
	description: str | None = None,
	company: str | None = None,
):
	"""Create or update an item (service or material) and its selling price."""
	_require(supabase_id, "supabase_id")
	_require(item_name, "item_name")
	vat_rate = int(vat_rate)
	if vat_rate not in VAT_RATES:
		frappe.throw(_("vat_rate must be one of {0}.").format(", ".join(map(str, VAT_RATES))))
	company = _company(company)

	name = _find("Item", supabase_id)
	doc = frappe.get_doc("Item", name) if name else frappe.new_doc("Item")
	if not name:
		doc.update(
			{
				"item_code": item_code or supabase_id,
				"stock_uom": uom,
				"is_stock_item": 0,
				"include_item_in_manufacturing": 0,
				"item_group": frappe.db.exists("Item Group", "Dienstleistungen") or get_root_of("Item Group"),
			}
		)
	doc.update({"supabase_id": supabase_id, "item_name": item_name, "description": description or item_name})
	doc.set("taxes", [{"item_tax_template": item_tax_template_name(company, vat_rate)}])
	doc.save()

	if rate is not None:
		_upsert_price(doc.name, flt(rate))
	return {"item_code": doc.name, "created": not name}


# Orders
# ------


@frappe.whitelist(methods=["POST"])
def create_order(
	supabase_id: str,
	customer: str,
	items: list,
	delivery_date: str | None = None,
	po_no: str | None = None,
	transaction_date: str | None = None,
	company: str | None = None,
):
	"""Create and submit a sales order. Idempotent on ``supabase_id``.

	``items``: ``[{"item": "<item supabase_id>", "qty": 2, "rate": 120.0, "description": "…"}]``"""
	_require(supabase_id, "supabase_id")
	existing = _find("Sales Order", supabase_id)
	if existing:
		order = frappe.get_doc("Sales Order", existing)
		return {"name": order.name, "created": False, "grand_total": flt(order.grand_total, 2)}

	company = _company(company)
	customer_name = _find("Customer", customer, required=True)
	order = frappe.new_doc("Sales Order")
	order.update(
		{
			"company": company,
			"customer": customer_name,
			"supabase_id": supabase_id,
			"transaction_date": transaction_date or nowdate(),
			"delivery_date": delivery_date or add_days(transaction_date or nowdate(), 30),
			"po_no": po_no,
			"selling_price_list": _price_list(),
		}
	)
	for row in _parse_items(items):
		row["delivery_date"] = order.delivery_date
		order.append("items", row)
	_apply_taxes(order, customer_name, company)
	order.insert()
	order.submit()
	return {"name": order.name, "created": True, "grand_total": flt(order.grand_total, 2)}


# Invoices
# --------


@frappe.whitelist(methods=["POST"])
def create_invoice(
	idempotency_key: str,
	customer: str,
	kind: str = "standard",
	order: str | None = None,
	items: list | None = None,
	amount: float | None = None,
	percent: float | None = None,
	posting_date: str | None = None,
	due_days: int = 14,
	service_period: str | None = None,
	supabase_id: str | None = None,
	submit: bool = True,
	company: str | None = None,
):
	"""Create (and by default submit) an invoice. Repeating a call with the same
	``idempotency_key`` returns the invoice created the first time.

	* ``standard``: ``items`` as for orders.
	* ``down_payment``: ``order`` plus ``amount`` (net) or ``percent`` of the order's net total.
	* ``final``: ``order``; ``items`` default to all items of the order. Received down payments
	  are deducted automatically."""
	_require(idempotency_key, "idempotency_key")
	existing = frappe.db.get_value("Sales Invoice", {"de_api_idempotency_key": idempotency_key}, "name")
	if existing:
		return {**invoice_summary(frappe.get_doc("Sales Invoice", existing)), "replayed": True}

	invoice_kind = _choice(INVOICE_KINDS, kind, "kind")
	company = _company(company)
	customer_name = _find("Customer", customer, required=True)
	sales_order = None
	if invoice_kind in (DOWN_PAYMENT, FINAL):
		_require(order, "order")
		sales_order = frappe.get_doc("Sales Order", _find("Sales Order", order, required=True))

	posting_date = getdate(posting_date or nowdate())
	doc = frappe.new_doc("Sales Invoice")
	doc.update(
		{
			"company": company,
			"customer": customer_name,
			"posting_date": posting_date,
			"set_posting_time": 1,
			"due_date": add_days(posting_date, int(due_days)),
			"de_invoice_kind": invoice_kind,
			"de_sales_order": sales_order.name if sales_order else None,
			"de_service_period": service_period,
			"supabase_id": supabase_id,
			"de_api_idempotency_key": idempotency_key,
			"selling_price_list": sales_order.selling_price_list if sales_order else _price_list(),
		}
	)

	if invoice_kind == STANDARD:
		for row in _parse_items(items, required=True):
			doc.append("items", row)
	elif invoice_kind == DOWN_PAYMENT:
		doc.append("items", _down_payment_row(sales_order, amount, percent))
	else:
		rows = _parse_items(items) if items else _order_rows(sales_order)
		for row in rows:
			doc.append("items", row)

	_apply_taxes(doc, customer_name, company, sales_order)
	try:
		doc.insert()
	except frappe.UniqueValidationError:
		# A concurrent call with the same key won the race.
		frappe.db.rollback()
		name = frappe.db.get_value("Sales Invoice", {"de_api_idempotency_key": idempotency_key}, "name")
		return {**invoice_summary(frappe.get_doc("Sales Invoice", name)), "replayed": True}
	if frappe.utils.cint(submit):
		doc.submit()
	return {**invoice_summary(doc), "replayed": False}


@frappe.whitelist(methods=["GET", "POST"])
def get_invoice(name: str | None = None, supabase_id: str | None = None):
	if not name:
		_require(supabase_id, "name or supabase_id")
		name = _find("Sales Invoice", supabase_id, required=True)
	doc = frappe.get_doc("Sales Invoice", name)
	doc.check_permission("read")
	return invoice_summary(doc)


@frappe.whitelist(methods=["GET", "POST"])
def get_invoice_pdf(name: str):
	"""The invoice as PDF (German layout), base64 encoded."""
	doc = frappe.get_doc("Sales Invoice", name)
	doc.check_permission("print")
	pdf = frappe.get_print("Sales Invoice", name, print_format=PRINT_FORMAT, doc=doc, as_pdf=True)
	return {"filename": f"{name}.pdf", "content_base64": base64.b64encode(pdf).decode()}


@frappe.whitelist(methods=["POST"])
def record_payment(
	invoice: str,
	amount: float,
	posting_date: str | None = None,
	reference_no: str | None = None,
	mode_of_payment: str | None = None,
):
	"""Book an incoming payment against an invoice (e.g. from a matched bank transaction)."""
	amount = flt(amount, 2)
	if amount <= 0:
		frappe.throw(_("amount must be greater than zero."))
	outstanding = flt(frappe.db.get_value("Sales Invoice", invoice, "outstanding_amount"), 2)
	if amount > outstanding:
		frappe.throw(
			_("amount {0} exceeds the outstanding amount {1} of {2}.").format(amount, outstanding, invoice)
		)

	posting_date = getdate(posting_date or nowdate())
	payment = get_payment_entry("Sales Invoice", invoice, party_amount=amount)
	payment.update(
		{
			"posting_date": posting_date,
			"reference_no": reference_no or invoice,
			"reference_date": posting_date,
			"mode_of_payment": mode_of_payment
			or (frappe.db.exists("Mode of Payment", "Überweisung") and "Überweisung"),
		}
	)
	payment.insert()
	payment.submit()
	return {
		**payment_summary(payment),
		"invoice_outstanding": flt(frappe.db.get_value("Sales Invoice", invoice, "outstanding_amount"), 2),
	}


# Helpers
# -------


def _require(value, label: str):
	if value in (None, "", []):
		frappe.throw(_("{0} is required.").format(label), frappe.MandatoryError)


def _choice(mapping: dict, value: str, label: str):
	if value not in mapping:
		frappe.throw(_("{0} must be one of: {1}.").format(label, ", ".join(mapping)))
	return mapping[value]


def _company(company: str | None) -> str:
	company = (
		company
		or frappe.defaults.get_user_default("Company")
		or frappe.db.get_single_value("Global Defaults", "default_company")
	)
	if not company:
		frappe.throw(_("No company given and no default company set."))
	return company


def _find(doctype: str, supabase_id: str, required: bool = False) -> str | None:
	name = frappe.db.get_value(doctype, {"supabase_id": supabase_id}, "name") if supabase_id else None
	if not name and required:
		frappe.throw(
			_("{0} with Supabase ID {1} not found.").format(_(doctype), supabase_id), frappe.DoesNotExistError
		)
	return name


def _parse_items(items, required: bool = False) -> list[dict]:
	items = frappe.parse_json(items) if items else []
	if required and not items:
		frappe.throw(_("items is required."), frappe.MandatoryError)
	rows = []
	for idx, item in enumerate(items, 1):
		item_code = _find("Item", item.get("item"), required=True)
		qty = flt(item.get("qty") or 1)
		if qty <= 0:
			frappe.throw(_("items[{0}]: qty must be greater than zero.").format(idx))
		row = {"item_code": item_code, "qty": qty}
		if item.get("rate") is not None:
			row["rate"] = flt(item["rate"])
		if item.get("description"):
			row["description"] = item["description"]
		rows.append(row)
	return rows


def _order_rows(order) -> list[dict]:
	return [
		{
			"item_code": row.item_code,
			"qty": row.qty,
			"rate": row.rate,
			"uom": row.uom,
			"description": row.description,
			"sales_order": order.name,
			"so_detail": row.name,
		}
		for row in order.items
	]


def _down_payment_row(order, amount, percent) -> dict:
	if amount in (None, "") and percent in (None, ""):
		frappe.throw(_("Give the down payment as amount (net) or percent."), frappe.MandatoryError)
	if amount not in (None, ""):
		net = flt(amount, 2)
		text = f"Abschlagszahlung zu Auftrag {order.name}"
	else:
		net = flt(flt(order.net_total) * flt(percent) / 100, 2)
		text = f"Abschlagszahlung zu Auftrag {order.name}: {flt(percent):g} % der Auftragssumme"
	if net <= 0:
		frappe.throw(_("The down payment must be greater than zero."))
	return {"item_code": DOWN_PAYMENT_ITEM, "qty": 1, "rate": net, "description": text}


def _apply_taxes(doc, customer: str, company: str, order=None):
	"""Sales tax template from the customer's tax category (Inland / § 13b / EU)."""
	if order and order.taxes_and_charges:
		template = order.taxes_and_charges
	else:
		category = frappe.db.get_value("Customer", customer, "tax_category") or "Inland"
		treatment = next((key for key, value in TAX_TREATMENTS.items() if value[0] == category), "domestic")
		template = sales_tax_template_name(company, treatment)
	doc.tax_category = frappe.db.get_value("Sales Taxes and Charges Template", template, "tax_category")
	doc.taxes_and_charges = template
	doc.set("taxes", [])
	for tax in get_taxes_and_charges("Sales Taxes and Charges Template", template):
		doc.append("taxes", tax)


def _price_list() -> str | None:
	return frappe.db.get_single_value("Selling Settings", "selling_price_list") or frappe.db.get_value(
		"Price List", {"selling": 1, "enabled": 1}, "name"
	)


def _upsert_price(item_code: str, rate: float):
	price_list = _price_list()
	name = frappe.db.get_value("Item Price", {"item_code": item_code, "price_list": price_list}, "name")
	price = frappe.get_doc("Item Price", name) if name else frappe.new_doc("Item Price")
	price.update({"item_code": item_code, "price_list": price_list, "price_list_rate": rate})
	price.save()
	# A new price is valid from today by default, so backdated orders would find none.
	if price.valid_from:
		price.db_set("valid_from", None)


def _upsert_address(customer, address: dict):
	_require(address.get("line1"), "address.line1")
	name = customer.customer_primary_address or frappe.db.get_value(
		"Dynamic Link",
		{"link_doctype": "Customer", "link_name": customer.name, "parenttype": "Address"},
		"parent",
	)
	doc = frappe.get_doc("Address", name) if name else frappe.new_doc("Address")
	doc.update(
		{
			"address_title": customer.customer_name,
			"address_type": "Billing",
			"address_line1": address.get("line1"),
			"address_line2": address.get("line2"),
			"pincode": address.get("pincode"),
			"city": address.get("city"),
			"country": address.get("country") or "Germany",
			"is_primary_address": 1,
		}
	)
	if not name:
		doc.append("links", {"link_doctype": "Customer", "link_name": customer.name})
	doc.save()
	if customer.customer_primary_address != doc.name:
		customer.db_set("customer_primary_address", doc.name)


def _upsert_contact(customer, email: str):
	name = frappe.db.get_value("Contact Email", {"email_id": email, "parenttype": "Contact"}, "parent")
	if not name:
		contact = frappe.get_doc(
			{
				"doctype": "Contact",
				"first_name": customer.customer_name,
				"email_ids": [{"email_id": email, "is_primary": 1}],
				"links": [{"link_doctype": "Customer", "link_name": customer.name}],
			}
		).insert()
		name = contact.name
	if customer.customer_primary_contact != name:
		customer.db_set("customer_primary_contact", name)
		customer.db_set("email_id", email)
