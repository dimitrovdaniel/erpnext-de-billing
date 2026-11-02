import base64

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt

from de_billing.api import v1
from de_billing.tests.utils import make_customer, make_down_payment, make_item, standard_order, uid


class TestAPI(FrappeTestCase):
	def test_upsert_customer_is_idempotent(self):
		supabase_id = uid("cust")
		first = v1.upsert_customer(supabase_id=supabase_id, customer_name="Bau GmbH")
		second = v1.upsert_customer(
			supabase_id=supabase_id,
			customer_name="Bau GmbH & Co. KG",
			tax_treatment="reverse_charge_13b",
			address={"line1": "Hafenstraße 1", "pincode": "20457", "city": "Hamburg"},
			email="rechnung@bau.example",
		)

		self.assertTrue(first["created"])
		self.assertFalse(second["created"])
		self.assertEqual(first["name"], second["name"])
		customer = frappe.get_doc("Customer", first["name"])
		self.assertEqual(customer.customer_name, "Bau GmbH & Co. KG")
		self.assertEqual(customer.tax_category, "§ 13b UStG")
		self.assertEqual(frappe.db.get_value("Address", customer.customer_primary_address, "city"), "Hamburg")
		self.assertEqual(customer.email_id, "rechnung@bau.example")

	def test_public_authority_requires_leitweg_id(self):
		with self.assertRaises(frappe.MandatoryError):
			v1.upsert_customer(
				supabase_id=uid("cust"), customer_name="Stadt Musterstadt", kind="public_authority"
			)
		result = v1.upsert_customer(
			supabase_id=uid("cust"),
			customer_name="Stadt Musterstadt",
			kind="public_authority",
			leitweg_id="04011000-1234512345-06",
		)
		self.assertEqual(
			frappe.db.get_value("Customer", result["name"], "customer_group"), "Öffentliche Auftraggeber"
		)

	def test_create_invoice_is_idempotent(self):
		customer, customer_name = make_customer()
		item = make_item(120)
		key = uid("idem")

		first = v1.create_invoice(idempotency_key=key, customer=customer, items=[{"item": item, "qty": 2}])
		second = v1.create_invoice(idempotency_key=key, customer=customer, items=[{"item": item, "qty": 5}])

		self.assertFalse(first["replayed"])
		self.assertTrue(second["replayed"])
		self.assertEqual(first["name"], second["name"])
		self.assertTrue(first["name"].startswith("RE-"))
		self.assertEqual(first["grand_total"], 285.60)  # 2 x 120.00 + 19 %
		self.assertEqual(frappe.db.count("Sales Invoice", {"customer": customer_name}), 1)

	def test_down_payment_by_percent(self):
		customer, _name = make_customer()
		order = standard_order(customer)
		result = v1.create_invoice(
			idempotency_key=uid("idem"), customer=customer, kind="down_payment", order=order, percent=30
		)
		self.assertEqual(result["kind"], "down_payment")
		self.assertEqual(result["net_total"], 3000.00)
		self.assertEqual(result["grand_total"], 3570.00)

	def test_record_payment_and_get_invoice(self):
		customer, _name = make_customer()
		order = standard_order(customer)
		dp = make_down_payment(customer, order, 3000)

		with self.assertRaises(frappe.ValidationError):
			v1.record_payment(invoice=dp, amount=5000)
		payment = v1.record_payment(invoice=dp, amount=1000, reference_no="BANK-4711")

		self.assertEqual(payment["invoice_outstanding"], 2570.00)
		self.assertEqual(payment["invoices"], [{"invoice": dp, "allocated": 1000.00}])
		self.assertEqual(v1.get_invoice(name=dp)["outstanding_amount"], 2570.00)

	def test_unknown_references_are_rejected(self):
		with self.assertRaises(frappe.DoesNotExistError):
			v1.create_invoice(idempotency_key=uid("idem"), customer="does-not-exist", items=[{"item": "x"}])
		with self.assertRaises(frappe.ValidationError):
			v1.create_invoice(idempotency_key=uid("idem"), customer="x", kind="credit")

	def test_invoice_pdf(self):
		customer, _name = make_customer()
		order = standard_order(customer)
		dp = make_down_payment(customer, order, 3000)

		result = v1.get_invoice_pdf(name=dp)

		self.assertEqual(result["filename"], f"{dp}.pdf")
		self.assertTrue(base64.b64decode(result["content_base64"]).startswith(b"%PDF"))

	def test_final_invoice_summary(self):
		customer, _name = make_customer()
		order = standard_order(customer)
		dp = make_down_payment(customer, order, 3000)
		v1.record_payment(invoice=dp, amount=3570)

		summary = v1.create_invoice(idempotency_key=uid("idem"), customer=customer, kind="final", order=order)

		self.assertEqual(summary["kind"], "final")
		self.assertEqual(summary["total_before_deductions"], 11900.00)
		self.assertEqual(summary["prepaid_amount"], 3570.00)
		self.assertEqual(summary["grand_total"], 8330.00)
		self.assertEqual(summary["deductions"][0]["down_payment_invoice"], dp)
		self.assertEqual(flt(summary["deductions"][0]["vat"]), 570.00)
