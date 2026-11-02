import frappe
from frappe.tests.utils import FrappeTestCase

from de_billing.setup.constants import EXTRA_ACCOUNTS, TAX_TREATMENTS


class TestCompanySetup(FrappeTestCase):
	def test_new_skr03_company_is_set_up(self):
		company = frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": "Nordbau Haustechnik GmbH",
				"abbr": "NBH",
				"country": "Germany",
				"default_currency": "EUR",
				"create_chart_of_accounts_based_on": "Standard Template",
				"chart_of_accounts": "SKR03 mit Kontonummern",
			}
		).insert()

		for number, _name, _parent, _type in EXTRA_ACCOUNTS:
			self.assertTrue(
				frappe.db.exists("Account", {"company": company.name, "account_number": number}), number
			)
		self.assertTrue(frappe.db.exists("Item Tax Template", "0 % - NBH"))
		self.assertEqual(
			frappe.db.get_value("Company", company.name, "default_income_account"),
			frappe.db.get_value("Account", {"company": company.name, "account_number": "8400"}),
		)
		for category, template in TAX_TREATMENTS.values():
			self.assertEqual(
				frappe.db.get_value(
					"Tax Rule", {"company": company.name, "tax_category": category}, "sales_tax_template"
				),
				f"{template} - NBH",
			)
