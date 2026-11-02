// Copyright (c) 2026, Daniel Dimitrov and contributors
// For license information, please see license.txt

frappe.ui.form.on("Webhook Delivery", {
	refresh(frm) {
		if (frm.doc.status !== "Delivered") {
			frm.add_custom_button(__("Retry now"), () =>
				frm.call("retry_now").then(() => frm.reload_doc())
			);
		}
	},
});
