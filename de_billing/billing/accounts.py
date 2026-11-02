"""Account and VAT-rate resolution for German invoices."""

import frappe

from de_billing.setup.company import get_account, item_tax_template_name
from de_billing.setup.constants import DOWN_PAYMENT_ACCOUNTS, OUTPUT_VAT_ACCOUNTS, REVENUE_ACCOUNTS


def get_tax_category(doc) -> str | None:
	"""Tax category of an invoice: taken from its sales tax template, falling back to the field."""
	if doc.get("taxes_and_charges"):
		category = frappe.get_cached_value(
			"Sales Taxes and Charges Template", doc.taxes_and_charges, "tax_category"
		)
		if category:
			return category
	return doc.get("tax_category") or None


def is_domestic(category: str | None) -> bool:
	return category in (None, "", "Inland")


def item_vat_rate(company: str, item_tax_template: str | None) -> int:
	"""Output VAT rate (19 / 7 / 0) an item tax template applies."""
	if not item_tax_template:
		return 0
	for number, rate in OUTPUT_VAT_ACCOUNTS.items():
		account = get_account(company, number, throw=False)
		tax_rate = frappe.db.get_value(
			"Item Tax Template Detail", {"parent": item_tax_template, "tax_type": account}, "tax_rate"
		)
		if tax_rate:
			return rate
	return 0


def effective_vat_rate(doc, row) -> int:
	"""VAT rate that actually applies to a row: 0 for reverse-charge and EU sales."""
	if not is_domestic(get_tax_category(doc)):
		return 0
	return item_vat_rate(doc.company, row.get("item_tax_template"))


def revenue_account(doc, row) -> str | None:
	category = get_tax_category(doc) or "Inland"
	mapping = REVENUE_ACCOUNTS.get(category)
	if mapping is None:
		return None
	if isinstance(mapping, dict):
		number = mapping.get(item_vat_rate(doc.company, row.get("item_tax_template")))
	else:
		number = mapping
	return get_account(doc.company, number, throw=False) if number else None


def down_payment_account(company: str, rate: int) -> str:
	return get_account(company, DOWN_PAYMENT_ACCOUNTS[rate])


def down_payment_accounts(company: str) -> set[str]:
	return {get_account(company, number, throw=False) for number in DOWN_PAYMENT_ACCOUNTS.values()}


def zero_item_tax_template(company: str) -> str:
	return item_tax_template_name(company, 0)


def rate_item_tax_template(company: str, rate: int) -> str:
	return item_tax_template_name(company, rate)
