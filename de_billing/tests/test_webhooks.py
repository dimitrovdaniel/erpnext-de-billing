import json
from unittest.mock import MagicMock, patch

import frappe
import requests
from frappe.tests.utils import FrappeTestCase
from frappe.utils import get_datetime, now_datetime

from de_billing.tests.utils import make_customer, make_down_payment, pay, standard_order
from de_billing.webhooks.delivery import backoff, deliver, deliver_due, sign

SECRET = "test-signing-secret"
URL = "https://office.example.test/hooks/erp"


def response(status_code: int, text: str = ""):
	return MagicMock(status_code=status_code, text=text)


class TestWebhooks(FrappeTestCase):
	def setUp(self):
		settings = frappe.get_single("DE Billing Settings")
		settings.update(
			{"enable_webhooks": 1, "webhook_url": URL, "webhook_secret": SECRET, "max_attempts": 3}
		)
		settings.save()

	def tearDown(self):
		settings = frappe.get_single("DE Billing Settings")
		settings.enable_webhooks = 0
		settings.save()

	def deliveries(self, reference: str) -> list:
		return frappe.get_all(
			"Webhook Delivery",
			filters={"reference_name": reference},
			fields=["name", "event", "status"],
			order_by="creation asc",
		)

	def make_event(self) -> tuple[str, str]:
		customer, _name = make_customer()
		invoice = make_down_payment(customer, standard_order(customer), 1000)
		[delivery] = self.deliveries(invoice)
		return invoice, delivery.name

	def test_submit_creates_event(self):
		invoice, delivery = self.make_event()
		doc = frappe.get_doc("Webhook Delivery", delivery)
		body = json.loads(doc.payload)

		self.assertEqual(doc.event, "invoice.submitted")
		self.assertEqual(doc.status, "Pending")
		self.assertEqual(body["id"], doc.event_id)
		self.assertEqual(body["data"]["name"], invoice)
		self.assertEqual(body["data"]["kind"], "down_payment")
		self.assertEqual(body["data"]["grand_total"], 1190.00)

	def test_delivery_is_signed(self):
		_invoice, delivery = self.make_event()
		with patch("de_billing.webhooks.delivery.requests.post", return_value=response(200)) as post:
			deliver(delivery)

		(url,), kwargs = post.call_args
		headers = kwargs["headers"]
		self.assertEqual(url, URL)
		self.assertEqual(
			headers["X-Webhook-Signature"],
			"sha256=" + sign(SECRET, headers["X-Webhook-Timestamp"], kwargs["data"]),
		)
		self.assertEqual(headers["X-Webhook-Event"], "invoice.submitted")
		doc = frappe.get_doc("Webhook Delivery", delivery)
		self.assertEqual((doc.status, doc.attempts, doc.last_status_code), ("Delivered", 1, 200))

	def test_failed_delivery_is_retried_with_backoff_then_given_up(self):
		_invoice, delivery = self.make_event()

		with patch("de_billing.webhooks.delivery.requests.post", return_value=response(503, "maintenance")):
			deliver(delivery)
		doc = frappe.get_doc("Webhook Delivery", delivery)
		self.assertEqual((doc.status, doc.attempts, doc.last_status_code), ("Pending", 1, 503))
		self.assertGreater(get_datetime(doc.next_attempt_at), now_datetime())

		# Not due yet: the scheduler leaves it alone.
		with patch("de_billing.webhooks.delivery.requests.post") as post:
			deliver_due()
		post.assert_not_called()

		with patch(
			"de_billing.webhooks.delivery.requests.post", side_effect=requests.ConnectionError("refused")
		):
			deliver(delivery)
			deliver(delivery)
		doc = frappe.get_doc("Webhook Delivery", delivery)
		self.assertEqual((doc.status, doc.attempts), ("Failed", 3))
		self.assertIn("ConnectionError", doc.last_error)

	def test_delivered_events_are_not_sent_twice(self):
		_invoice, delivery = self.make_event()
		with patch("de_billing.webhooks.delivery.requests.post", return_value=response(200)) as post:
			deliver(delivery)
			deliver(delivery)
		self.assertEqual(post.call_count, 1)

	def test_payment_events(self):
		invoice, _delivery = self.make_event()
		pay(invoice, 1190)

		events = [d.event for d in self.deliveries(invoice)]
		self.assertEqual(events, ["invoice.submitted", "invoice.paid"])
		payment = frappe.get_value("Payment Entry", {"reference_no": invoice}, "name")
		self.assertEqual([d.event for d in self.deliveries(payment)], ["payment.received"])

	def test_backoff(self):
		self.assertEqual([backoff(n).total_seconds() for n in (1, 2, 3, 4)], [60, 120, 240, 480])
		self.assertEqual(backoff(20).total_seconds(), 6 * 3600)
