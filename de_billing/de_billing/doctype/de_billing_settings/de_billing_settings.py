# Copyright (c) 2026, Daniel Dimitrov and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class DEBillingSettings(Document):
	def validate(self):
		if self.enable_webhooks and not (self.webhook_url or "").startswith(("https://", "http://")):
			frappe.throw(_("The webhook endpoint must be an http(s) URL."))
		if (self.max_attempts or 0) < 1:
			self.max_attempts = 1
		if (self.timeout_seconds or 0) < 1:
			self.timeout_seconds = 10
