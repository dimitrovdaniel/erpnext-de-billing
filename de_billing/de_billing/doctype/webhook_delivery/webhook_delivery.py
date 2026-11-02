# Copyright (c) 2026, Daniel Dimitrov and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class WebhookDelivery(Document):
	@frappe.whitelist()
	def retry_now(self):
		"""Put an undelivered event back in the queue and try it straight away."""
		from de_billing.webhooks.delivery import deliver

		frappe.only_for(("System Manager", "Accounts Manager"))
		self.db_set({"status": "Pending", "next_attempt_at": None})
		deliver(self.name)
		self.reload()
		return self.status
