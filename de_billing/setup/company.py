"""Per-company setup for German companies on the SKR03 chart. Every step is idempotent."""

import frappe
from frappe import _

from de_billing.setup.constants import (
	DOWN_PAYMENT_ITEM,
	EXTRA_ACCOUNTS,
	ITEM_TAX_TEMPLATE_TITLES,
	OUTPUT_VAT_ACCOUNTS,
	TAX_TREATMENTS,
)


def on_company_update(doc, method=None):
	"""Set up new German SKR03 companies as soon as ERPNext has created their chart of accounts."""
	if not is_german_skr03_company(doc.name):
		return
	if frappe.db.exists("Account", {"company": doc.name, "account_number": "1718"}):
		return
	setup_company(doc.name)


def is_german_skr03_company(company: str) -> bool:
	country, chart = frappe.get_cached_value("Company", company, ["country", "chart_of_accounts"])
	return country == "Germany" and "SKR03" in (chart or "")


def setup_company(company: str):
	if not frappe.db.exists("Account", {"company": company, "account_number": "1776"}):
		# The chart of accounts is created by ERPNext after the company itself; nothing to do yet.
		return
	create_extra_accounts(company)
	ensure_zero_item_tax_template(company)
	link_tax_categories(company)
	frappe.db.set_value("Company", company, "default_income_account", get_account(company, "8400"))
	frappe.clear_cache(doctype="Company")


def get_account(company: str, number: str, throw: bool = True) -> str | None:
	account = frappe.db.get_value(
		"Account", {"company": company, "account_number": number, "is_group": 0}, "name"
	)
	if not account and throw:
		frappe.throw(_("Account {0} is missing in the chart of accounts of {1}.").format(number, company))
	return account


def create_extra_accounts(company: str):
	for number, account_name, parent_name, account_type in EXTRA_ACCOUNTS:
		if frappe.db.exists("Account", {"company": company, "account_number": number}):
			continue
		parent = frappe.db.get_value(
			"Account", {"company": company, "account_name": parent_name, "is_group": 1}, "name"
		)
		if not parent:
			frappe.throw(
				_("Account group {0} not found in {1}. Is the company on the SKR03 chart?").format(
					parent_name, company
				)
			)
		frappe.get_doc(
			{
				"doctype": "Account",
				"company": company,
				"account_name": account_name,
				"account_number": number,
				"parent_account": parent,
				"account_type": account_type,
				"is_group": 0,
			}
		).insert(ignore_permissions=True)


def item_tax_template_name(company: str, rate: int) -> str:
	abbr = frappe.get_cached_value("Company", company, "abbr")
	return f"{ITEM_TAX_TEMPLATE_TITLES[rate]} - {abbr}"


def ensure_zero_item_tax_template(company: str):
	"""Down-payment deductions are booked with 0 % item tax; their VAT is reversed explicitly."""
	name = item_tax_template_name(company, 0)
	if frappe.db.exists("Item Tax Template", name):
		return
	doc = frappe.get_doc(
		{"doctype": "Item Tax Template", "title": ITEM_TAX_TEMPLATE_TITLES[0], "company": company}
	)
	for number in OUTPUT_VAT_ACCOUNTS:
		doc.append("taxes", {"tax_type": get_account(company, number), "tax_rate": 0})
	doc.insert(ignore_permissions=True)


def sales_tax_template_name(company: str, treatment: str) -> str:
	abbr = frappe.get_cached_value("Company", company, "abbr")
	return f"{TAX_TREATMENTS[treatment][1]} - {abbr}"


def link_tax_categories(company: str):
	"""Tie ERPNext's German sales tax templates to tax categories, so a customer's tax category
	selects the right template (Tax Rule) and the right revenue account (see billing.accounts)."""
	for treatment, (category, _template) in TAX_TREATMENTS.items():
		if not frappe.db.exists("Tax Category", category):
			frappe.get_doc({"doctype": "Tax Category", "title": category}).insert(ignore_permissions=True)

		template = sales_tax_template_name(company, treatment)
		if not frappe.db.exists("Sales Taxes and Charges Template", template):
			continue
		frappe.db.set_value("Sales Taxes and Charges Template", template, "tax_category", category)

		if not frappe.db.exists(
			"Tax Rule", {"company": company, "tax_type": "Sales", "tax_category": category}
		):
			frappe.get_doc(
				{
					"doctype": "Tax Rule",
					"tax_type": "Sales",
					"company": company,
					"tax_category": category,
					"sales_tax_template": template,
					"priority": 1,
				}
			).insert(ignore_permissions=True)


def get_down_payment_item() -> str:
	return DOWN_PAYMENT_ITEM
