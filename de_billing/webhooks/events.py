"""Business events sent to the office app."""

import frappe
from frappe.utils import flt

from de_billing.webhooks.delivery import enqueue_event
from de_billing.webhooks.payloads import invoice_summary, payment_summary


def sales_invoice_submitted(doc, method=None):
	enqueue_event(
		"credit_note.submitted" if doc.is_return else "invoice.submitted", doc, invoice_summary(doc)
	)


def sales_invoice_cancelled(doc, method=None):
	enqueue_event(
		"credit_note.cancelled" if doc.is_return else "invoice.cancelled", doc, invoice_summary(doc)
	)


def payment_entry_submitted(doc, method=None):
	if doc.payment_type != "Receive" or doc.party_type != "Customer":
		return
	enqueue_event("payment.received", doc, payment_summary(doc))
	for ref in doc.references:
		if ref.reference_doctype != "Sales Invoice":
			continue
		invoice = frappe.get_doc("Sales Invoice", ref.reference_name)
		if flt(invoice.outstanding_amount, 2) <= 0:
			enqueue_event("invoice.paid", invoice, invoice_summary(invoice))


def payment_entry_cancelled(doc, method=None):
	if doc.payment_type != "Receive" or doc.party_type != "Customer":
		return
	enqueue_event("payment.cancelled", doc, payment_summary(doc))
