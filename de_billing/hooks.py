app_name = "de_billing"
app_title = "DE Billing"
app_publisher = "Daniel Dimitrov"
app_description = "German invoicing for ERPNext: SKR03 setup, down-payment and final invoices, reverse charge, REST API and signed webhooks"
app_email = "daniel@codebydaniel.net"
app_license = "mit"
required_apps = ["erpnext"]

# Installation
# ------------

after_install = "de_billing.setup.install.after_install"
after_migrate = "de_billing.setup.install.after_migrate"

# Jinja (print formats)
# ---------------------

jinja = {
	"methods": ["de_billing.billing.printing.get_invoice_context"],
}

# Document events
# ---------------

doc_events = {
	"Company": {
		"on_update": "de_billing.setup.company.on_company_update",
	},
	"Customer": {
		"validate": "de_billing.billing.customer.validate",
	},
	"Item": {
		"validate": "de_billing.billing.item.validate",
	},
	"Sales Invoice": {
		"before_insert": "de_billing.billing.invoice.before_insert",
		"before_validate": "de_billing.billing.invoice.before_validate",
		"validate": "de_billing.billing.invoice.validate",
		"before_submit": "de_billing.billing.invoice.before_submit",
		"on_submit": [
			"de_billing.billing.invoice.on_submit",
			"de_billing.webhooks.events.sales_invoice_submitted",
		],
		"before_cancel": "de_billing.billing.invoice.before_cancel",
		"on_cancel": "de_billing.webhooks.events.sales_invoice_cancelled",
	},
	"Payment Entry": {
		"on_submit": "de_billing.webhooks.events.payment_entry_submitted",
		"on_cancel": "de_billing.webhooks.events.payment_entry_cancelled",
	},
}

# Scheduled tasks
# ---------------

scheduler_events = {
	"cron": {
		"* * * * *": ["de_billing.webhooks.delivery.deliver_due"],
	},
}
