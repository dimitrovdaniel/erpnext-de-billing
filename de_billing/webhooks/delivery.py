"""Outbox for webhooks: every event is stored first, then delivered with an HMAC signature.

Delivery is at-least-once. Receivers should de-duplicate on the ``X-Webhook-Id`` header and
verify ``X-Webhook-Signature`` (see ``tools/webhook_receiver.py``)."""

import hashlib
import hmac
import json
import time
import uuid
from datetime import timedelta

import frappe
import requests
from frappe.utils import add_to_date, now_datetime

USER_AGENT = "de_billing-webhooks/1.0"
MAX_BACKOFF = timedelta(hours=6)


def settings():
	return frappe.get_cached_doc("DE Billing Settings")


def enqueue_event(event: str, doc, data: dict) -> str | None:
	"""Store an event in the outbox and deliver it right after the current transaction commits."""
	if not settings().enable_webhooks:
		return None

	event_id = str(uuid.uuid4())
	body = {
		"id": event_id,
		"event": event,
		"created_at": now_datetime().isoformat(timespec="seconds"),
		"data": data,
	}
	delivery = frappe.get_doc(
		{
			"doctype": "Webhook Delivery",
			"event": event,
			"event_id": event_id,
			"reference_doctype": doc.doctype,
			"reference_name": doc.name,
			"status": "Pending",
			"next_attempt_at": now_datetime(),
			"payload": json.dumps(body, indent=2, ensure_ascii=False, default=str),
		}
	)
	delivery.insert(ignore_permissions=True)
	if not frappe.flags.in_test:
		frappe.enqueue(
			"de_billing.webhooks.delivery.deliver",
			queue="short",
			delivery=delivery.name,
			enqueue_after_commit=True,
		)
	return delivery.name


def sign(secret: str, timestamp: str, body: bytes) -> str:
	"""HMAC-SHA256 over "<timestamp>.<raw body>", hex encoded."""
	message = timestamp.encode() + b"." + body
	return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def backoff(attempts: int) -> timedelta:
	"""1, 2, 4, 8 … minutes after each failed attempt, capped at 6 hours."""
	return min(timedelta(minutes=2 ** max(attempts - 1, 0)), MAX_BACKOFF)


def deliver(delivery: str):
	conf = settings()
	if not conf.enable_webhooks:
		return

	row = frappe.db.sql(
		"select status from `tabWebhook Delivery` where name = %s for update", delivery, as_dict=True
	)
	if not row or row[0].status != "Pending":
		return

	doc = frappe.get_doc("Webhook Delivery", delivery)
	body = doc.payload.encode()
	timestamp = str(int(time.time()))
	headers = {
		"Content-Type": "application/json",
		"User-Agent": USER_AGENT,
		"X-Webhook-Id": doc.event_id,
		"X-Webhook-Event": doc.event,
		"X-Webhook-Timestamp": timestamp,
		"X-Webhook-Signature": "sha256=" + sign(conf.get_password("webhook_secret"), timestamp, body),
	}

	status_code, error = None, None
	try:
		response = requests.post(
			conf.webhook_url, data=body, headers=headers, timeout=conf.timeout_seconds or 10
		)
		status_code = response.status_code
		if not 200 <= status_code < 300:
			error = f"HTTP {status_code}: {response.text[:300]}"
	except requests.RequestException as e:
		error = f"{type(e).__name__}: {str(e)[:300]}"

	attempts = (doc.attempts or 0) + 1
	values = {"attempts": attempts, "last_status_code": status_code or 0, "last_error": error}
	if error is None:
		values.update({"status": "Delivered", "delivered_at": now_datetime(), "next_attempt_at": None})
	elif attempts >= (conf.max_attempts or 8):
		values.update({"status": "Failed", "next_attempt_at": None})
	else:
		values["next_attempt_at"] = add_to_date(now_datetime(), seconds=backoff(attempts).total_seconds())
	doc.db_set(values, update_modified=False)
	if not frappe.flags.in_test:
		frappe.db.commit()


def deliver_due():
	"""Scheduler job (every minute): retry pending deliveries whose next attempt is due."""
	if not settings().enable_webhooks:
		return
	due = frappe.get_all(
		"Webhook Delivery",
		filters={"status": "Pending", "next_attempt_at": ("<=", now_datetime())},
		order_by="creation asc",
		limit=100,
		pluck="name",
	)
	for name in due:
		deliver(name)
