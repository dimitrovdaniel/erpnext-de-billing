from frappe.tests.utils import FrappeTestCase

from de_billing.billing.printing import german_number, get_invoice_context
from de_billing.tests.utils import (
	closing_documents,
	invoice,
	make_customer,
	make_down_payment,
	make_final,
	pay,
	standard_order,
)


class TestPrinting(FrappeTestCase):
	def test_final_invoice_layout(self):
		customer, _name = make_customer()
		order = standard_order(customer)
		dp1 = make_down_payment(customer, order, 3000)
		dp2 = make_down_payment(customer, order, 3000)
		pay(dp1, 3570)
		final = invoice(make_final(customer, order))

		context = get_invoice_context(final)

		self.assertEqual(context["title"], "Schlussrechnung")
		self.assertEqual(context["gross_services"], "11.900,00 €")
		self.assertEqual(context["prepaid"], "3.570,00 €")
		self.assertEqual(context["grand_total"], "8.330,00 €")
		# Only received payments are listed as deductions; the unpaid one is mentioned as closed.
		self.assertEqual([d["invoice"] for d in context["deductions"]], [dp1])
		self.assertTrue(any(dp2 in note for note in context["notes"]))

		# The down payment closed by the final invoice says so instead of claiming it was paid.
		dp_context = get_invoice_context(invoice(dp2))
		self.assertIn(final.name, dp_context["payment_note"])
		self.assertEqual(dp_context["title"], "2. Abschlagsrechnung")

	def test_closing_correction_invoice_shows_reversed_vat(self):
		customer, _name = make_customer()
		order = standard_order(customer)
		dp = make_down_payment(customer, order, 3000)
		pay(dp, 2000)
		[credit_note] = closing_documents(make_final(customer, order))

		context = get_invoice_context(invoice(credit_note))

		self.assertEqual(context["title"], "Rechnungskorrektur")
		self.assertEqual(context["positions"][0]["vat"], "19 %")
		self.assertEqual(context["vat_lines"][0]["vat_value"], -250.67)
		self.assertEqual(context["grand_total"], "-1.570,00 €")

	def test_legal_notes(self):
		customer, _name = make_customer(tax_treatment="reverse_charge_13b")
		final = invoice(make_final(customer, standard_order(customer)))
		self.assertIn("§ 13b UStG", " ".join(get_invoice_context(final)["notes"]))

		customer, _name = make_customer(kind="private")
		final = invoice(make_final(customer, standard_order(customer)))
		self.assertIn("§ 14b", " ".join(get_invoice_context(final)["notes"]))

	def test_german_number(self):
		self.assertEqual(
			[german_number(v) for v in (10, 1.5, 1234.5, 420, 0.125)],
			["10", "1,5", "1.234,5", "420", "0,125"],
		)
