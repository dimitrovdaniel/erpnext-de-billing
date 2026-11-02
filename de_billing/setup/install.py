import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.custom.doctype.property_setter.property_setter import make_property_setter
from frappe.utils.nestedset import get_root_of

from de_billing.setup.constants import (
	CORRECTION_SERIES,
	CUSTOMER_GROUPS,
	DOWN_PAYMENT_ITEM,
	DOWN_PAYMENT_ITEM_GROUP,
	NAMING_SERIES,
	ORDER_SERIES,
	UOMS,
)
from de_billing.setup.custom_fields import get_custom_fields


def after_install():
	setup_site()
	for company in frappe.get_all("Company", pluck="name"):
		from de_billing.setup.company import is_german_skr03_company, setup_company

		if is_german_skr03_company(company):
			setup_company(company)


def after_migrate():
	create_custom_fields(get_custom_fields(), ignore_validate=True)
	setup_naming_series()
	setup_print_defaults()


def setup_site():
	create_custom_fields(get_custom_fields(), ignore_validate=True)
	setup_naming_series()
	setup_print_defaults()
	create_uoms()
	create_customer_groups()
	create_down_payment_item()
	# German invoices show exact amounts; ERPNext would otherwise add a rounded total.
	frappe.db.set_single_value("Global Defaults", "disable_rounded_total", 1)
	# Final invoices deduct down payments as negative rows (ERPNext rejects negative quantities).
	frappe.db.set_single_value("Selling Settings", "allow_negative_rates_for_items", 1)
	# VAT rows come from the sales tax template. Without this, ERPNext would add VAT rows from the
	# item tax template on reverse-charge (§ 13b) and EU invoices, whose templates have no rows.
	frappe.db.set_single_value("Accounts Settings", "add_taxes_from_item_tax_template", 0)
	frappe.db.set_single_value("DE Billing Settings", "close_unpaid_down_payments", 1)


def setup_naming_series():
	"""RE (invoice), AR (down payment), SR (final), RK (correction) and AB (order confirmation).
	ERPNext's own series stay available."""
	_prepend_series("Sales Invoice", [*NAMING_SERIES.values(), CORRECTION_SERIES], NAMING_SERIES["Standard"])
	_prepend_series("Sales Order", [ORDER_SERIES], ORDER_SERIES)


def _prepend_series(doctype: str, series: list[str], default: str):
	existing = (frappe.get_meta(doctype).get_field("naming_series").options or "").split("\n")
	options = series + [s for s in existing if s and s not in series]
	make_property_setter(
		doctype, "naming_series", "options", "\n".join(options), "Text", validate_fields_for_doctype=False
	)
	make_property_setter(
		doctype, "naming_series", "default", default, "Text", validate_fields_for_doctype=False
	)


def setup_print_defaults():
	if frappe.db.exists("Print Format", "Rechnung DE"):
		make_property_setter(
			"Sales Invoice",
			None,
			"default_print_format",
			"Rechnung DE",
			"Data",
			for_doctype=True,
			validate_fields_for_doctype=False,
		)


def create_uoms():
	for uom in UOMS:
		if not frappe.db.exists("UOM", uom):
			frappe.get_doc({"doctype": "UOM", "uom_name": uom, "must_be_whole_number": 0}).insert(
				ignore_permissions=True
			)


def create_customer_groups():
	root = get_root_of("Customer Group")
	for group in CUSTOMER_GROUPS.values():
		if not frappe.db.exists("Customer Group", group):
			frappe.get_doc(
				{"doctype": "Customer Group", "customer_group_name": group, "parent_customer_group": root}
			).insert(ignore_permissions=True)


def create_down_payment_item():
	if not frappe.db.exists("Item Group", DOWN_PAYMENT_ITEM_GROUP):
		frappe.get_doc(
			{
				"doctype": "Item Group",
				"item_group_name": DOWN_PAYMENT_ITEM_GROUP,
				"parent_item_group": get_root_of("Item Group"),
			}
		).insert(ignore_permissions=True)

	if not frappe.db.exists("Item", DOWN_PAYMENT_ITEM):
		# No item tax template on purpose: the VAT rate of a down payment is set per invoice row.
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": DOWN_PAYMENT_ITEM,
				"item_name": "Abschlagszahlung",
				"item_group": DOWN_PAYMENT_ITEM_GROUP,
				"stock_uom": "Pauschal",
				"is_stock_item": 0,
				"include_item_in_manufacturing": 0,
				"description": "Abschlagszahlung / Anzahlung auf einen Auftrag",
			}
		).insert(ignore_permissions=True)
