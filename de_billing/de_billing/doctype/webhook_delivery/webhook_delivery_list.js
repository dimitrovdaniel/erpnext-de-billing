frappe.listview_settings["Webhook Delivery"] = {
	get_indicator(doc) {
		const colors = { Pending: "orange", Delivered: "green", Failed: "red" };
		return [__(doc.status), colors[doc.status], "status,=," + doc.status];
	},
};
