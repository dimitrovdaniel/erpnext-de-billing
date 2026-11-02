"""Minimal webhook receiver that verifies de_billing signatures — a reference for the office app.

    WEBHOOK_SECRET=... python tools/webhook_receiver.py --port 8787

Checks, in this order: signature (HMAC-SHA256 over "<timestamp>.<raw body>"), timestamp
tolerance (replay protection) and the event id (de-duplication, delivery is at-least-once).
Only the standard library is used, so the same logic ports directly to a Supabase Edge Function.
"""

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOLERANCE_SECONDS = 300
seen_ids: set[str] = set()


def verify(secret: str, timestamp: str, body: bytes, signature_header: str) -> bool:
	expected = (
		"sha256=" + hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
	)
	return hmac.compare_digest(expected, signature_header or "")


class Handler(BaseHTTPRequestHandler):
	secret = ""

	def do_POST(self):
		body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
		timestamp = self.headers.get("X-Webhook-Timestamp", "")
		event_id = self.headers.get("X-Webhook-Id", "")

		if not verify(self.secret, timestamp, body, self.headers.get("X-Webhook-Signature", "")):
			return self.reply(401, "invalid signature")
		if not timestamp.isdigit() or abs(time.time() - int(timestamp)) > TOLERANCE_SECONDS:
			return self.reply(400, "stale timestamp")
		if event_id in seen_ids:
			return self.reply(200, "duplicate, ignored")
		seen_ids.add(event_id)

		event = json.loads(body)
		data = event["data"]
		amount = data.get("grand_total", data.get("amount", 0))
		name = data.get("name", "")
		line = (
			f"{time.strftime('%H:%M:%S')}  signature ok  {event['event']:<22} {name:<20} {amount:>12,.2f} EUR"
		)
		sys.stdout.write(line + "\n")  # one write per event, so concurrent deliveries do not interleave
		sys.stdout.flush()
		self.reply(200, "ok")

	def reply(self, status: int, text: str):
		self.send_response(status)
		self.send_header("Content-Type", "text/plain")
		self.end_headers()
		self.wfile.write(text.encode())

	def log_message(self, *args):
		pass


if __name__ == "__main__":
	parser = argparse.ArgumentParser()
	parser.add_argument("--port", type=int, default=8787)
	args = parser.parse_args()
	Handler.secret = os.environ.get("WEBHOOK_SECRET") or parser.error("set WEBHOOK_SECRET")
	print(f"listening on :{args.port}", flush=True)
	ThreadingHTTPServer(("0.0.0.0", args.port), Handler).serve_forever()
