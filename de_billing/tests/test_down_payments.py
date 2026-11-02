"""Down-payment and final invoices: amounts must reconcile to the cent."""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt

from de_billing.billing.invoice import ensure_not_deducted_elsewhere
from de_billing.tests.utils import (
	balance,
	closing_documents,
	invoice,
	make_customer,
	make_down_payment,
	make_final,
	make_item,
	make_order,
	pay,
	standard_order,
)


class TestDownPayments(FrappeTestCase):
	def setUp(self):
		self.customer, self.customer_name = make_customer()
		self.order = standard_order(self.customer)

	def test_down_payment_invoice(self):
		dp = invoice(make_down_payment(self.customer, self.order, 3000))

		self.assertTrue(dp.name.startswith("AR-"))
		self.assertEqual(dp.de_down_payment_no, 1)
		self.assertEqual(flt(dp.net_total), 3000.00)
		self.assertEqual(flt(dp.total_taxes_and_charges), 570.00)
		self.assertEqual(flt(dp.grand_total), 3570.00)
		# The net amount is a liability (received down payments), not revenue.
		self.assertEqual(balance("1718", [dp.name]), -3000.00)
		self.assertEqual(balance("1776", [dp.name]), -570.00)
		self.assertEqual(balance("8400", [dp.name]), 0)

		second = invoice(make_down_payment(self.customer, self.order, 1000))
		self.assertEqual(second.de_down_payment_no, 2)

	def test_all_down_payments_paid(self):
		dp1 = make_down_payment(self.customer, self.order, 3000)
		dp2 = make_down_payment(self.customer, self.order, 3000)
		pay(dp1, 3570)
		pay(dp2, 3570)

		final = invoice(make_final(self.customer, self.order))

		self.assertTrue(final.name.startswith("SR-"))
		self.assertEqual(len(final.de_down_payment_deductions), 2)
		self.assertEqual(flt(final.de_total_before_deductions), 11900.00)
		self.assertEqual(flt(final.de_prepaid_amount), 7140.00)
		self.assertEqual(flt(final.net_total), 4000.00)
		self.assertEqual(flt(final.total_taxes_and_charges), 760.00)
		self.assertEqual(flt(final.grand_total), 4760.00)
		self.assertEqual(flt(final.outstanding_amount), 4760.00)
		self.assertEqual(closing_documents(final.name), [])

		vouchers = [dp1, dp2, final.name]
		self.assertEqual(balance("1718", vouchers), 0)
		self.assertEqual(balance("1776", vouchers), -1900.00)
		self.assertEqual(balance("8400", vouchers), -10000.00)

	def test_partially_paid_down_payment(self):
		dp1 = make_down_payment(self.customer, self.order, 3000)
		dp2 = make_down_payment(self.customer, self.order, 3000)
		pay(dp1, 3570)
		pay(dp2, 2000)

		final = invoice(make_final(self.customer, self.order))
		rows = {row.down_payment_invoice: row for row in final.de_down_payment_deductions}

		self.assertEqual(flt(rows[dp2].paid_amount), 2000.00)
		self.assertEqual(flt(rows[dp2].tax_amount), 319.33)
		self.assertEqual(flt(rows[dp2].net_amount), 1680.67)
		self.assertEqual(flt(rows[dp2].unpaid_amount), 1570.00)
		self.assertEqual(flt(final.de_prepaid_amount), 5570.00)
		self.assertEqual(flt(final.total_taxes_and_charges), 1010.67)
		self.assertEqual(flt(final.grand_total), 6330.00)

		# The unpaid 1,570.00 are closed with a correction invoice, so they can never be dunned.
		[credit_note] = closing_documents(final.name)
		credit_note = invoice(credit_note)
		self.assertTrue(credit_note.name.startswith("RK-"))
		self.assertEqual(credit_note.de_invoice_kind, "Correction Invoice")
		self.assertEqual(credit_note.return_against, dp2)
		self.assertEqual(flt(credit_note.grand_total), -1570.00)
		self.assertEqual(flt(invoice(dp2).outstanding_amount), 0)

		vouchers = [dp1, dp2, final.name, credit_note.name]
		self.assertEqual(balance("1718", vouchers), 0)
		self.assertEqual(balance("1776", vouchers), -1900.00)
		self.assertEqual(balance("8400", vouchers), -10000.00)

	def test_unpaid_down_payment_is_closed_not_deducted(self):
		dp1 = make_down_payment(self.customer, self.order, 3000)
		dp2 = make_down_payment(self.customer, self.order, 3000)
		pay(dp1, 3570)

		final = invoice(make_final(self.customer, self.order))

		self.assertEqual(flt(final.de_prepaid_amount), 3570.00)
		self.assertEqual(flt(final.grand_total), 8330.00)
		self.assertEqual([row.de_deduction_for for row in final.items if row.de_deduction_for], [dp1])
		[credit_note] = closing_documents(final.name)
		self.assertEqual(flt(invoice(credit_note).grand_total), -3570.00)
		self.assertEqual(flt(invoice(dp2).outstanding_amount), 0)
		self.assertEqual(balance("1718", [dp1, dp2, final.name, credit_note]), 0)

	def test_payment_received_after_draft_is_deducted_on_submit(self):
		dp = make_down_payment(self.customer, self.order, 3000)
		final = invoice(make_final(self.customer, self.order, submit=False))
		self.assertEqual(flt(final.de_prepaid_amount), 0)

		pay(dp, 3570)
		final.reload()
		final.submit()

		self.assertEqual(flt(final.de_prepaid_amount), 3570.00)
		self.assertEqual(flt(final.grand_total), 8330.00)

	def test_no_double_deduction(self):
		from de_billing.api import v1

		dp = make_down_payment(self.customer, self.order, 3000)
		pay(dp, 3570)
		first = invoice(make_final(self.customer, self.order, submit=False))
		# A second final invoice for the same order, e.g. for additional work.
		second = invoice(
			v1.create_invoice(
				idempotency_key=frappe.generate_hash(),
				customer=self.customer,
				kind="final",
				order=self.order,
				items=[{"item": make_item(5000), "qty": 1, "rate": 5000}],
				submit=False,
			)["name"]
		)
		self.assertEqual(flt(second.de_prepaid_amount), 3570.00)

		first.submit()
		second.reload()
		second.submit()

		self.assertEqual(flt(first.de_prepaid_amount), 3570.00)
		self.assertEqual(flt(second.de_prepaid_amount), 0)
		self.assertEqual(flt(second.grand_total), 5950.00)

		# The guard also holds if a stale deduction row reaches submission.
		second.append("de_down_payment_deductions", {"down_payment_invoice": dp, "paid_amount": 3570})
		self.assertRaises(frappe.ValidationError, ensure_not_deducted_elsewhere, second)

	def test_down_payments_exceeding_services_are_rejected(self):
		from de_billing.api import v1

		dp = make_down_payment(self.customer, self.order, 3000)
		pay(dp, 3570)
		with self.assertRaisesRegex(frappe.ValidationError, "exceed the services"):
			v1.create_invoice(
				idempotency_key=frappe.generate_hash(),
				customer=self.customer,
				kind="final",
				order=self.order,
				items=[{"item": make_item(100), "qty": 1, "rate": 100}],
			)

	def test_cancel_final_reopens_closed_down_payments(self):
		dp = make_down_payment(self.customer, self.order, 3000)
		pay(dp, 2000)
		final = invoice(make_final(self.customer, self.order))
		[credit_note] = closing_documents(final.name)

		final.cancel()

		self.assertEqual(invoice(credit_note).docstatus, 2)
		self.assertEqual(flt(invoice(dp).outstanding_amount), 1570.00)

	def test_deducted_down_payment_cannot_be_cancelled(self):
		dp = make_down_payment(self.customer, self.order, 3000)
		pay(dp, 3570)
		make_final(self.customer, self.order)
		frappe.get_doc("Payment Entry", {"reference_no": dp}).cancel()

		self.assertRaises(frappe.LinkExistsError, invoice(dp).cancel)

	def test_down_payment_item_only_on_down_payment_invoices(self):
		doc = frappe.new_doc("Sales Invoice")
		doc.update({"customer": self.customer_name, "de_invoice_kind": "Standard"})
		doc.append("items", {"item_code": "ABSCHLAG", "qty": 1, "rate": 100})

		with self.assertRaisesRegex(frappe.ValidationError, "can only be used on a down-payment invoice"):
			doc.insert()


class TestTaxTreatments(FrappeTestCase):
	def test_reverse_charge_13b(self):
		customer, _name = make_customer(tax_treatment="reverse_charge_13b")
		order = standard_order(customer)
		dp = invoice(make_down_payment(customer, order, 3000))
		self.assertEqual(flt(dp.total_taxes_and_charges), 0)
		self.assertEqual(balance("1710", [dp.name]), -3000.00)
		pay(dp.name, 3000)

		final = invoice(make_final(customer, order))

		self.assertEqual(flt(final.total_taxes_and_charges), 0)
		self.assertEqual(flt(final.grand_total), 7000.00)
		self.assertEqual(balance("8337", [final.name]), -10000.00)
		self.assertEqual(balance("1710", [dp.name, final.name]), 0)

	def test_revenue_accounts_by_vat_rate(self):
		customer, _name = make_customer()
		order = make_order(
			customer, [(make_item(100, vat_rate=19), 2, 100), (make_item(50, vat_rate=7), 4, 50)]
		)
		final = invoice(make_final(customer, order))

		self.assertEqual(flt(final.total_taxes_and_charges), 52.00)  # 19 % of 200 + 7 % of 200
		self.assertEqual(balance("8400", [final.name]), -200.00)
		self.assertEqual(balance("8300", [final.name]), -200.00)

	def test_mixed_rate_order_needs_explicit_down_payment_rate(self):
		customer, _name = make_customer()
		order = make_order(
			customer, [(make_item(100, vat_rate=19), 1, 100), (make_item(50, vat_rate=7), 1, 50)]
		)
		with self.assertRaises(frappe.ValidationError):
			make_down_payment(customer, order, 50)

	def test_eu_services(self):
		customer, _name = make_customer(tax_treatment="eu_services", tax_id="NL123456789B01")
		order = standard_order(customer)
		final = invoice(make_final(customer, order))

		self.assertEqual(flt(final.total_taxes_and_charges), 0)
		self.assertEqual(balance("8338", [final.name]), -10000.00)
