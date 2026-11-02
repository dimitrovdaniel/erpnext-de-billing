"""Factories for tests. Everything goes through the public API, so the API is exercised as well."""

import frappe
from frappe.utils import flt

from de_billing.api import v1


def company() -> str:
	return frappe.db.get_single_value("Global Defaults", "default_company")


def uid(prefix: str) -> str:
	return f"{prefix}-{frappe.generate_hash(length=10)}"


def make_customer(tax_treatment: str = "domestic", kind: str = "business", **kwargs) -> tuple[str, str]:
	supabase_id = uid("cust")
	result = v1.upsert_customer(
		supabase_id=supabase_id,
		customer_name=kwargs.pop("customer_name", f"Kunde {supabase_id}"),
		kind=kind,
		tax_treatment=tax_treatment,
		**kwargs,
	)
	return supabase_id, result["name"]


def make_item(rate: float, vat_rate: int = 19, uom: str = "Stk") -> str:
	supabase_id = uid("item")
	v1.upsert_item(
		supabase_id=supabase_id, item_name=f"Leistung {supabase_id}", rate=rate, vat_rate=vat_rate, uom=uom
	)
	return supabase_id


def make_order(customer: str, lines: list[tuple[str, float, float]]) -> str:
	"""lines: [(item supabase_id, qty, rate)] -> order supabase_id"""
	supabase_id = uid("order")
	v1.create_order(
		supabase_id=supabase_id,
		customer=customer,
		items=[{"item": item, "qty": qty, "rate": rate} for item, qty, rate in lines],
	)
	return supabase_id


def standard_order(customer: str) -> str:
	"""10 h assembly at 85.00 + one boiler at 9,150.00 = 10,000.00 net, 19 % VAT."""
	return make_order(customer, [(make_item(85), 10, 85), (make_item(9150), 1, 9150)])


def make_down_payment(customer: str, order: str, amount: float) -> str:
	return v1.create_invoice(
		idempotency_key=uid("idem"), customer=customer, kind="down_payment", order=order, amount=amount
	)["name"]


def make_final(customer: str, order: str, submit: bool = True) -> str:
	return v1.create_invoice(
		idempotency_key=uid("idem"), customer=customer, kind="final", order=order, submit=submit
	)["name"]


def pay(invoice: str, amount: float):
	return v1.record_payment(invoice=invoice, amount=amount)


def invoice(name: str):
	return frappe.get_doc("Sales Invoice", name)


def account(number: str) -> str:
	return frappe.db.get_value(
		"Account", {"company": company(), "account_number": number, "is_group": 0}, "name"
	)


def balance(account_number: str, vouchers: list[str]) -> float:
	"""Debit minus credit on an account for the given vouchers."""
	value = frappe.db.sql(
		"""select coalesce(sum(debit - credit), 0) from `tabGL Entry`
		where account = %s and voucher_no in %s and is_cancelled = 0""",
		(account(account_number), tuple(vouchers)),
	)[0][0]
	return flt(value, 2)


def closing_documents(final_invoice: str) -> list[str]:
	return [
		row.closing_document
		for row in invoice(final_invoice).de_down_payment_deductions
		if row.closing_document
	]
