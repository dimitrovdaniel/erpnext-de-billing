"""JSON representations shared by the REST API and webhooks."""

import frappe
from frappe.utils import flt


def invoice_summary(doc) -> dict:
	customer_supabase_id = frappe.db.get_value("Customer", doc.customer, "supabase_id")
	return {
		"name": doc.name,
		"supabase_id": doc.get("supabase_id"),
		"kind": "credit_note" if doc.is_return else _kind_key(doc.get("de_invoice_kind")),
		"status": doc.status,
		"docstatus": doc.docstatus,
		"customer": doc.customer,
		"customer_supabase_id": customer_supabase_id,
		"order": doc.get("de_sales_order"),
		"down_payment_no": doc.get("de_down_payment_no") or None,
		"return_against": doc.get("return_against"),
		"closes_down_payment_for": doc.get("de_closing_for"),
		"posting_date": str(doc.posting_date),
		"due_date": str(doc.due_date) if doc.due_date else None,
		"currency": doc.currency,
		"net_total": flt(doc.net_total, 2),
		"total_taxes": flt(doc.total_taxes_and_charges, 2),
		"grand_total": flt(doc.grand_total, 2),
		"outstanding_amount": flt(doc.outstanding_amount, 2),
		"total_before_deductions": flt(doc.get("de_total_before_deductions"), 2) or None,
		"prepaid_amount": flt(doc.get("de_prepaid_amount"), 2),
		"deductions": [
			{
				"down_payment_invoice": row.down_payment_invoice,
				"invoiced": flt(row.invoiced_amount, 2),
				"received": flt(row.paid_amount, 2),
				"net": flt(row.net_amount, 2),
				"vat": flt(row.tax_amount, 2),
				"unpaid_closed": flt(row.unpaid_amount, 2),
				"closing_document": row.closing_document,
			}
			for row in doc.get("de_down_payment_deductions") or []
		],
	}


def payment_summary(doc) -> dict:
	return {
		"name": doc.name,
		"customer": doc.party,
		"posting_date": str(doc.posting_date),
		"amount": flt(doc.paid_amount, 2),
		"currency": doc.paid_from_account_currency,
		"reference_no": doc.reference_no,
		"invoices": [
			{"invoice": ref.reference_name, "allocated": flt(ref.allocated_amount, 2)}
			for ref in doc.references
			if ref.reference_doctype == "Sales Invoice"
		],
	}


def _kind_key(kind: str | None) -> str:
	return {"Down Payment Invoice": "down_payment", "Final Invoice": "final"}.get(kind or "", "standard")
