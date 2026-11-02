"""Down-payment (Abschlagsrechnung) and final invoice (Schlussrechnung) handling for Sales Invoice.

Booking model
-------------
* A down-payment invoice contains only the down-payment item. Its net amount is credited to a
  "received down payments" liability account (SKR03 1718 / 1711 / 1710 by VAT rate), and VAT is
  charged on it as usual.
* A final invoice lists all services of the order at full value (revenue accounts) and then
  deducts every down payment that was actually received:
    - one negative row per down-payment invoice, booked back against the liability account with
      0 % item tax, and
    - one "Actual" tax row per VAT account with the VAT contained in the received amount.
  The VAT on the final invoice is therefore exactly the VAT on the services minus the VAT already
  invoiced on the received down payments, and the amount due is exactly the gross total of the
  services minus the gross amounts received. No rounding drift, no double deduction.
* The unpaid part of a down-payment invoice is closed on submission of the final invoice with a
  correction invoice (credit note against that down-payment invoice), so it can never be dunned
  and the liability account nets to zero.
"""

from collections import defaultdict

import frappe
from erpnext.controllers.accounts_controller import get_taxes_and_charges
from erpnext.stock.get_item_details import get_item_tax_map
from frappe import _
from frappe.utils import flt, formatdate

from de_billing.billing.accounts import (
	down_payment_account,
	effective_vat_rate,
	revenue_account,
	zero_item_tax_template,
)
from de_billing.setup.constants import (
	CORRECTION_SERIES,
	DOWN_PAYMENT_ITEM,
	NAMING_SERIES,
	OUTPUT_VAT_ACCOUNTS,
	REVENUE_ACCOUNTS,
)

STANDARD = "Standard"
DOWN_PAYMENT = "Down Payment Invoice"
FINAL = "Final Invoice"
CORRECTION = "Correction Invoice"

_AUTO_SERIES = {*NAMING_SERIES.values(), CORRECTION_SERIES, "ACC-SINV-.YYYY.-", "ACC-SINV-RET-.YYYY.-"}


def kind_of(doc) -> str:
	return doc.get("de_invoice_kind") or STANDARD


# Document events
# ---------------


def before_insert(doc, method=None):
	if frappe.db.get_single_value("Global Defaults", "disable_rounded_total"):
		doc.disable_rounded_total = 1
	if doc.is_return:
		doc.de_invoice_kind = CORRECTION
		if not doc.naming_series or doc.naming_series in _AUTO_SERIES:
			doc.naming_series = CORRECTION_SERIES
		return

	if not doc.naming_series or doc.naming_series in _AUTO_SERIES:
		doc.naming_series = NAMING_SERIES[kind_of(doc)]


def before_validate(doc, method=None):
	if doc.is_return:
		return

	kind = kind_of(doc)
	if kind in (DOWN_PAYMENT, FINAL) and not doc.de_sales_order:
		orders = {row.sales_order for row in doc.items if row.get("sales_order")}
		if len(orders) == 1:
			doc.de_sales_order = orders.pop()

	if kind == DOWN_PAYMENT:
		_strip_deductions(doc)
		prepare_down_payment(doc)
	elif kind == FINAL:
		prepare_final(doc)
	else:
		_strip_deductions(doc)


def validate(doc, method=None):
	if doc.is_return:
		return

	kind = kind_of(doc)
	for row in doc.items:
		if row.item_code == DOWN_PAYMENT_ITEM and kind != DOWN_PAYMENT and not row.get("de_deduction_for"):
			frappe.throw(
				_("Row {0}: the item {1} can only be used on a down-payment invoice.").format(
					row.idx, DOWN_PAYMENT_ITEM
				)
			)

	set_revenue_accounts(doc)

	if kind == FINAL:
		doc.de_total_before_deductions = flt(doc.grand_total + flt(doc.de_prepaid_amount), 2)
	else:
		doc.de_total_before_deductions = 0
		doc.de_prepaid_amount = 0


def before_submit(doc, method=None):
	if kind_of(doc) == FINAL and not doc.is_return:
		ensure_not_deducted_elsewhere(doc)


def on_submit(doc, method=None):
	if kind_of(doc) != FINAL or doc.is_return:
		return
	if not frappe.db.get_single_value("DE Billing Settings", "close_unpaid_down_payments"):
		return
	for row in doc.de_down_payment_deductions:
		if flt(row.unpaid_amount) > 0:
			credit_note = make_closing_credit_note(doc, row)
			row.db_set("closing_document", credit_note.name, update_modified=False)


def before_cancel(doc, method=None):
	"""Cancelling a final invoice reopens the down payments it closed."""
	if kind_of(doc) != FINAL or doc.is_return:
		return
	for row in doc.de_down_payment_deductions:
		if (
			row.closing_document
			and frappe.db.get_value("Sales Invoice", row.closing_document, "docstatus") == 1
		):
			credit_note = frappe.get_doc("Sales Invoice", row.closing_document)
			credit_note.flags.ignore_permissions = True
			# The only link to it is this final invoice, which is being cancelled.
			credit_note.flags.ignore_links = True
			credit_note.cancel()


# Down-payment invoices
# ---------------------


def prepare_down_payment(doc):
	order = _get_order(doc)
	if not doc.items:
		frappe.throw(_("Add the down-payment amount as a row with the item {0}.").format(DOWN_PAYMENT_ITEM))
	if not doc.taxes_and_charges and order.taxes_and_charges:
		doc.taxes_and_charges = order.taxes_and_charges

	default_template = _single_item_tax_template(order)
	for row in doc.items:
		if row.item_code != DOWN_PAYMENT_ITEM:
			frappe.throw(
				_("Row {0}: a down-payment invoice may only contain the item {1}.").format(
					row.idx, DOWN_PAYMENT_ITEM
				)
			)
		if not row.item_tax_template:
			if not default_template:
				frappe.throw(
					_(
						"Row {0}: order {1} contains items with different VAT rates. Set the item tax template of the down payment row."
					).format(row.idx, order.name)
				)
			row.item_tax_template = default_template
		row.item_tax_rate = get_item_tax_map(doc.company, row.item_tax_template, as_json=True)
		row.income_account = down_payment_account(doc.company, effective_vat_rate(doc, row))

	if not doc.de_down_payment_no:
		previous = frappe.db.count(
			"Sales Invoice",
			{
				"de_sales_order": order.name,
				"de_invoice_kind": DOWN_PAYMENT,
				"is_return": 0,
				"docstatus": ("<", 2),
				"name": ("!=", doc.name or ""),
			},
		)
		doc.de_down_payment_no = previous + 1


# Final invoices
# --------------


def prepare_final(doc):
	order = _get_order(doc)
	_strip_deductions(doc)

	if not doc.taxes_and_charges and order.taxes_and_charges:
		doc.taxes_and_charges = order.taxes_and_charges
	if not doc.taxes and doc.taxes_and_charges:
		for tax in get_taxes_and_charges("Sales Taxes and Charges Template", doc.taxes_and_charges):
			doc.append("taxes", tax)

	if not doc.items:
		frappe.throw(_("Add the services of order {0} to the final invoice.").format(order.name))
	for row in doc.items:
		if row.item_code == DOWN_PAYMENT_ITEM:
			frappe.throw(
				_("Row {0}: down payments are deducted automatically, remove the item {1}.").format(
					row.idx, DOWN_PAYMENT_ITEM
				)
			)

	down_payments = get_open_down_payments(doc, lock=doc._action == "submit")
	zero_template = zero_item_tax_template(doc.company)
	zero_tax_map = get_item_tax_map(doc.company, zero_template, as_json=True)
	vat_deducted = defaultdict(float)
	prepaid = 0.0

	for dp in down_payments:
		split = split_payment(dp)
		doc.append(
			"de_down_payment_deductions",
			{
				"down_payment_invoice": dp.name,
				"posting_date": dp.posting_date,
				"invoiced_amount": dp.grand_total,
				"paid_amount": split.paid,
				"net_amount": split.net,
				"tax_amount": split.vat,
				"unpaid_amount": split.unpaid,
			},
		)
		if not split.paid:
			continue

		dp_row = dp.items[0]
		doc.append(
			"items",
			{
				"item_code": DOWN_PAYMENT_ITEM,
				"item_name": f"Abzüglich Abschlag {dp.name}",
				"description": "abzüglich {}. Abschlagsrechnung {} vom {}, Zahlungseingang {}".format(
					dp.de_down_payment_no or "",
					dp.name,
					formatdate(dp.posting_date),
					frappe.format_value(split.paid, {"fieldtype": "Currency", "options": "currency"}, dp),
				),
				"qty": 1,
				"rate": -split.net,
				"uom": dp_row.uom,
				"stock_uom": dp_row.stock_uom,
				"conversion_factor": 1,
				"income_account": dp_row.income_account,
				"cost_center": dp_row.cost_center,
				"item_tax_template": zero_template,
				"item_tax_rate": zero_tax_map,
				"de_deduction_for": dp.name,
			},
		)
		for account, amount in split.tax_by_account.items():
			vat_deducted[account] += amount
		prepaid += split.paid

	for account, amount in vat_deducted.items():
		amount = flt(amount, 2)
		if not amount:
			continue
		doc.append(
			"taxes",
			{
				"charge_type": "Actual",
				"account_head": account,
				"description": "abzüglich {} aus erhaltenen Abschlagszahlungen".format(
					frappe.db.get_value("Account", account, "account_name")
				),
				"tax_amount": -amount,
				"cost_center": doc.cost_center
				or frappe.get_cached_value("Company", doc.company, "cost_center"),
				"de_deduction_row": 1,
			},
		)

	doc.de_prepaid_amount = flt(prepaid, 2)
	_check_deductions_within_services(doc)


def _check_deductions_within_services(doc):
	services = [row for row in doc.items if not row.get("de_deduction_for")]
	if any(row.rate in (None, "") for row in services):
		return  # rates are still to be fetched; ERPNext validates the total afterwards
	services_net = flt(sum(flt(row.qty) * flt(row.rate) for row in services), 2)
	deducted_net = flt(-sum(flt(row.rate) for row in doc.items if row.get("de_deduction_for")), 2)
	if deducted_net > services_net:
		frappe.throw(
			_(
				"The down payments received ({0} net) exceed the services on this final invoice ({1} net). Bill the full order on the final invoice, or refund the difference with a correction invoice."
			).format(deducted_net, services_net),
			title=_("Down payments exceed services"),
		)


def get_open_down_payments(doc, lock: bool = False) -> list:
	"""Submitted down-payment invoices of the order that no other submitted final invoice deducted."""
	names = frappe.get_all(
		"Sales Invoice",
		filters={
			"de_sales_order": doc.de_sales_order,
			"de_invoice_kind": DOWN_PAYMENT,
			"is_return": 0,
			"docstatus": 1,
			"customer": doc.customer,
			"company": doc.company,
		},
		order_by="posting_date asc, creation asc",
		pluck="name",
	)
	if not names:
		return []
	if lock:
		# Serialise concurrent final invoices for the same order.
		frappe.db.sql("select name from `tabSales Invoice` where name in %s for update", [tuple(names)])

	deducted = set(_deducted_elsewhere(names, exclude=doc.name))
	return [frappe.get_doc("Sales Invoice", name) for name in names if name not in deducted]


def ensure_not_deducted_elsewhere(doc):
	names = [row.down_payment_invoice for row in doc.de_down_payment_deductions]
	if not names:
		return
	duplicates = _deducted_elsewhere(names, exclude=doc.name)
	if duplicates:
		frappe.throw(
			_("Down-payment invoice {0} is already deducted on another final invoice.").format(
				", ".join(sorted(set(duplicates)))
			),
			title=_("Double deduction"),
		)


def _deducted_elsewhere(names: list[str], exclude: str | None) -> list[str]:
	return frappe.get_all(
		"Down Payment Deduction",
		filters={
			"parenttype": "Sales Invoice",
			"down_payment_invoice": ("in", names),
			"docstatus": 1,
			"parent": ("!=", exclude or ""),
		},
		pluck="down_payment_invoice",
	)


def received_amount(invoice: str) -> float:
	"""Payments actually received on an invoice (payment entries and journal entries, not credit notes)."""
	via_payment_entries = frappe.db.sql(
		"""select coalesce(sum(ref.allocated_amount), 0)
		from `tabPayment Entry Reference` ref
		join `tabPayment Entry` pe on pe.name = ref.parent
		where ref.reference_doctype = 'Sales Invoice' and ref.reference_name = %s
			and pe.docstatus = 1 and pe.payment_type = 'Receive'""",
		invoice,
	)[0][0]
	via_journal_entries = frappe.db.sql(
		"""select coalesce(sum(jea.credit_in_account_currency - jea.debit_in_account_currency), 0)
		from `tabJournal Entry Account` jea
		join `tabJournal Entry` je on je.name = jea.parent
		where jea.reference_type = 'Sales Invoice' and jea.reference_name = %s and je.docstatus = 1""",
		invoice,
	)[0][0]
	return flt(via_payment_entries) + flt(via_journal_entries)


def split_payment(dp, paid: float | None = None) -> frappe._dict:
	"""Split the amount received on a down-payment invoice into net and VAT per VAT account.

	Fully paid invoices are taken over exactly. For partial payments the VAT is pro-rated per
	account and the net is the remainder, so net + VAT always equals the amount received."""
	gross = flt(dp.grand_total, 2)
	if paid is None:
		paid = received_amount(dp.name)
	paid = min(flt(paid, 2), gross)

	invoiced_tax = defaultdict(float)
	for tax in dp.taxes:
		invoiced_tax[tax.account_head] += flt(tax.tax_amount_after_discount_amount)

	if paid == gross:
		tax_by_account = {account: flt(amount, 2) for account, amount in invoiced_tax.items()}
	elif gross:
		tax_by_account = {account: flt(amount * paid / gross, 2) for account, amount in invoiced_tax.items()}
	else:
		tax_by_account = {}
	tax_by_account = {account: amount for account, amount in tax_by_account.items() if amount}

	vat = flt(sum(tax_by_account.values()), 2)
	net = flt(paid - vat, 2)
	unpaid_tax = {
		account: flt(amount - tax_by_account.get(account, 0), 2) for account, amount in invoiced_tax.items()
	}
	return frappe._dict(
		paid=paid,
		net=net,
		vat=vat,
		tax_by_account=tax_by_account,
		unpaid=flt(gross - paid, 2),
		unpaid_net=flt(dp.net_total - net, 2),
		unpaid_tax_by_account={account: amount for account, amount in unpaid_tax.items() if amount},
	)


def make_closing_credit_note(final_invoice, deduction_row):
	"""Correction invoice for the unpaid part of a down-payment invoice, booked against it."""
	dp = frappe.get_doc("Sales Invoice", deduction_row.down_payment_invoice)
	split = split_payment(dp, paid=deduction_row.paid_amount)
	dp_row = dp.items[0]
	zero_template = zero_item_tax_template(dp.company)

	credit_note = frappe.new_doc("Sales Invoice")
	credit_note.update(
		{
			"company": dp.company,
			"customer": dp.customer,
			"currency": dp.currency,
			"conversion_rate": dp.conversion_rate,
			"selling_price_list": dp.selling_price_list,
			"debit_to": dp.debit_to,
			"cost_center": dp.cost_center,
			"posting_date": final_invoice.posting_date,
			"set_posting_time": 1,
			"is_return": 1,
			"return_against": dp.name,
			"update_outstanding_for_self": 0,
			"naming_series": CORRECTION_SERIES,
			"taxes_and_charges": dp.taxes_and_charges,
			"de_sales_order": dp.de_sales_order,
			"de_closing_for": final_invoice.name,
			"remarks": f"Ausbuchung des nicht gezahlten Betrags der Abschlagsrechnung {dp.name}, abgerechnet mit Schlussrechnung {final_invoice.name}.",
		}
	)
	credit_note.append(
		"items",
		{
			"item_code": DOWN_PAYMENT_ITEM,
			"item_name": dp_row.item_name,
			"description": "Nicht gezahlter Betrag der {}. Abschlagsrechnung {} vom {}, ausgebucht mit Schlussrechnung {}".format(
				dp.de_down_payment_no or "", dp.name, formatdate(dp.posting_date), final_invoice.name
			),
			"qty": -1,
			"rate": split.unpaid_net,
			"uom": dp_row.uom,
			"stock_uom": dp_row.stock_uom,
			"conversion_factor": 1,
			"income_account": dp_row.income_account,
			"cost_center": dp_row.cost_center,
			"item_tax_template": zero_template,
			"item_tax_rate": get_item_tax_map(dp.company, zero_template, as_json=True),
		},
	)
	for account, amount in split.unpaid_tax_by_account.items():
		credit_note.append(
			"taxes",
			{
				"charge_type": "Actual",
				"account_head": account,
				"description": frappe.db.get_value("Account", account, "account_name"),
				"tax_amount": -amount,
				"cost_center": dp.cost_center,
			},
		)
	credit_note.flags.ignore_permissions = True
	credit_note.insert()
	credit_note.submit()
	return credit_note


# Revenue accounts
# ----------------


def set_revenue_accounts(doc):
	"""Book each service row to the SKR03 revenue account of its tax treatment and VAT rate
	(8400 / 8300 domestic, 8337 § 13b, 8125 EU goods, 8338 EU services). Accounts set by hand to
	something else are left alone."""
	managed = _managed_revenue_accounts(doc.company)
	changed = False
	for row in doc.items:
		if row.item_code == DOWN_PAYMENT_ITEM or row.get("de_deduction_for"):
			continue
		account = revenue_account(doc, row)
		if (
			account
			and row.income_account != account
			and (not row.income_account or row.income_account in managed)
		):
			row.income_account = account
			changed = True
	if changed:
		doc.set_against_income_account()


def _managed_revenue_accounts(company: str) -> set:
	from de_billing.setup.company import get_account

	numbers = set()
	for mapping in REVENUE_ACCOUNTS.values():
		numbers.update(mapping.values() if isinstance(mapping, dict) else [mapping])
	accounts = {get_account(company, number, throw=False) for number in numbers}
	accounts.add(frappe.get_cached_value("Company", company, "default_income_account"))
	return {account for account in accounts if account}


# Helpers
# -------


def _get_order(doc):
	if not doc.de_sales_order:
		frappe.throw(_("Select the order this invoice belongs to."))
	order = frappe.get_doc("Sales Order", doc.de_sales_order)
	if order.docstatus != 1:
		frappe.throw(_("Order {0} must be submitted.").format(order.name))
	if order.customer != doc.customer or order.company != doc.company:
		frappe.throw(_("Order {0} belongs to a different customer or company.").format(order.name))
	return order


def _single_item_tax_template(order) -> str | None:
	templates = {row.item_tax_template for row in order.items if row.item_tax_template}
	return templates.pop() if len(templates) == 1 else None


def _strip_deductions(doc):
	items = [row for row in doc.items if not row.get("de_deduction_for")]
	taxes = [tax for tax in doc.taxes if not tax.get("de_deduction_row")]
	if len(items) != len(doc.items):
		doc.set("items", items)
	if len(taxes) != len(doc.taxes):
		doc.set("taxes", taxes)
	for idx, row in enumerate(doc.items, 1):
		row.idx = idx
	for idx, tax in enumerate(doc.taxes, 1):
		tax.idx = idx
	doc.set("de_down_payment_deductions", [])
	doc.de_prepaid_amount = 0


def vat_rate_of_account(company: str, account: str) -> int:
	from de_billing.setup.company import get_account

	for number, rate in OUTPUT_VAT_ACCOUNTS.items():
		if get_account(company, number, throw=False) == account:
			return rate
	return 0
