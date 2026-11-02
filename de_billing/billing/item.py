import frappe

from de_billing.setup.constants import TAX_TREATMENTS


def validate(doc, method=None):
	"""ERPNext only applies an item tax template whose tax category matches the transaction exactly.
	Customers here always carry a tax category (Inland, § 13b, EU …), so a generic VAT row on an item
	is copied to every category. The VAT rate is then found no matter who the customer is."""
	categories = [category for category, _template in TAX_TREATMENTS.values()]
	existing = {(row.item_tax_template, row.tax_category or "") for row in doc.taxes}
	covered = {(_company(row.item_tax_template), row.tax_category) for row in doc.taxes if row.tax_category}

	for row in [row for row in doc.taxes if not row.tax_category]:
		company = _company(row.item_tax_template)
		for category in categories:
			if (company, category) in covered or (row.item_tax_template, category) in existing:
				continue
			doc.append(
				"taxes",
				{
					"item_tax_template": row.item_tax_template,
					"tax_category": category,
					"valid_from": row.valid_from,
					"minimum_net_rate": row.minimum_net_rate,
					"maximum_net_rate": row.maximum_net_rate,
				},
			)
			covered.add((company, category))


def _company(item_tax_template: str) -> str | None:
	return frappe.get_cached_value("Item Tax Template", item_tax_template, "company")
