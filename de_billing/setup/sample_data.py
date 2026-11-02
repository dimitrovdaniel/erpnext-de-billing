"""Sample data for a fictional heating & plumbing contractor, created through the public API.

    bench --site <site> execute de_billing.setup.sample_data.create --kwargs "{'company': 'Muster Haustechnik GmbH'}"

Safe to run twice: everything is keyed on fixed Supabase IDs and idempotency keys.
"""

import frappe
from frappe.core.doctype.user.user import generate_keys

from de_billing.api import v1

COMPANY_DETAILS = {
	"phone_no": "+49 40 3861 4270",
	"email": "rechnung@muster-haustechnik.de",
	"website": "www.muster-haustechnik.de",
	"tax_id": "DE312845679",
	"company_description": "Heizung · Sanitär · Energietechnik",
	"registration_details": "Amtsgericht Hamburg HRB 184 233\nGeschäftsführerin: Jana Muster\nSteuernummer 27/641/03518",
}

CUSTOMERS = [
	{
		"supabase_id": "c_nordlicht",
		"customer_name": "Bauträger Nordlicht GmbH",
		"tax_id": "DE287346110",
		"email": "kreditoren@nordlicht-bau.de",
		"address": {"line1": "Am Sandtorkai 41", "pincode": "20457", "city": "Hamburg"},
	},
	{
		"supabase_id": "c_musterhausen",
		"customer_name": "Stadt Musterhausen – Gebäudemanagement",
		"kind": "public_authority",
		"leitweg_id": "02000000-4711002300-37",
		"email": "e-rechnung@musterhausen.de",
		"address": {"line1": "Rathausplatz 1", "pincode": "22846", "city": "Musterhausen"},
	},
	{
		"supabase_id": "c_hansabau",
		"customer_name": "Hansa Bau GmbH & Co. KG",
		"tax_treatment": "reverse_charge_13b",
		"tax_id": "DE298104552",
		"email": "rechnungseingang@hansa-bau.de",
		"address": {"line1": "Billstraße 80", "pincode": "20539", "city": "Hamburg"},
	},
	{
		"supabase_id": "c_berger",
		"customer_name": "Sophie und Jonas Berger",
		"kind": "private",
		"email": "berger.familie@example.de",
		"address": {"line1": "Lindenweg 7", "pincode": "22397", "city": "Hamburg"},
	},
	{
		"supabase_id": "c_weber",
		"customer_name": "Zahnarztpraxis Dr. Weber",
		"email": "praxis@dr-weber-zahn.de",
		"address": {"line1": "Mühlenkamp 18", "pincode": "22303", "city": "Hamburg"},
	},
	{
		"supabase_id": "c_vandijk",
		"customer_name": "Bouwbedrijf Van Dijk B.V.",
		"tax_treatment": "eu_services",
		"tax_id": "NL854312987B01",
		"email": "facturen@vandijk-bouw.nl",
		"address": {
			"line1": "Keizersgracht 112",
			"pincode": "1015 CV",
			"city": "Amsterdam",
			"country": "Netherlands",
		},
	},
]

ITEMS = [
	("i_wp12", "WP-LW-12", "Luft/Wasser-Wärmepumpe 12 kW inkl. Pufferspeicher", 11450.00, "Stk"),
	("i_bwt24", "BWT-24", "Gas-Brennwerttherme 24 kW inkl. Abgassystem", 4890.00, "Stk"),
	("i_fbh", "FBH-NASS", "Fußbodenheizung Nasssystem inkl. Verlegung", 64.50, "m²"),
	("i_hk22", "HK-22-610", "Heizkörper Typ 22, 600 × 1000 mm, inkl. Ventil", 289.00, "Stk"),
	("i_mont", "MONT-STD", "Montage Heizung / Sanitär (Monteurstunde)", 78.00, "Std."),
	("i_hyd", "HYD-ABGL", "Hydraulischer Abgleich nach Verfahren B inkl. Protokoll", 690.00, "Pauschal"),
	("i_wart", "WART-HZG", "Jahreswartung Heizungsanlage", 189.00, "Pauschal"),
	("i_vwi", "SAN-VWI-WC", "Vorwandinstallation WC inkl. Spülkasten und Betätigungsplatte", 1240.00, "Stk"),
	("i_plan", "PLAN-TGA", "TGA-Fachplanung Heizung (Ingenieurstunde)", 95.00, "Std."),
]


def create(company: str = "Muster Haustechnik GmbH"):
	frappe.flags.mute_emails = True
	setup_company_details(company)
	for customer in CUSTOMERS:
		v1.upsert_customer(**customer)
	for supabase_id, item_code, name, rate, uom in ITEMS:
		v1.upsert_item(
			supabase_id=supabase_id, item_code=item_code, item_name=name, rate=rate, uom=uom, company=company
		)

	project_nordlicht(company)
	project_musterhausen(company)
	project_hansabau(company)
	standard_invoices(company)
	frappe.db.commit()


def setup_company_details(company: str):
	frappe.db.set_value("Company", company, COMPANY_DETAILS)
	if not frappe.db.exists(
		"Dynamic Link", {"link_doctype": "Company", "link_name": company, "parenttype": "Address"}
	):
		frappe.get_doc(
			{
				"doctype": "Address",
				"address_title": company,
				"address_type": "Office",
				"address_line1": "Hafenstraße 12",
				"pincode": "20457",
				"city": "Hamburg",
				"country": "Germany",
				"is_your_company_address": 1,
				"links": [{"link_doctype": "Company", "link_name": company}],
			}
		).insert()
	if not frappe.db.exists("Bank", "Hamburger Sparkasse"):
		frappe.get_doc(
			{"doctype": "Bank", "bank_name": "Hamburger Sparkasse", "swift_number": "HASPDEHHXXX"}
		).insert()
	if not frappe.db.exists("Bank Account", {"company": company, "is_company_account": 1}):
		frappe.get_doc(
			{
				"doctype": "Bank Account",
				"account_name": "Geschäftskonto",
				"bank": "Hamburger Sparkasse",
				"company": company,
				"is_company_account": 1,
				"is_default": 1,
				"account": frappe.db.get_value("Account", {"company": company, "account_number": "1200"}),
				"iban": "DE89370400440532013000",
				"branch_code": "HASPDEHHXXX",
			}
		).insert()


def project_nordlicht(company: str):
	"""Three down payments: one paid, one partly paid, one unpaid — then the final invoice."""
	order = "o_nordlicht_elbblick"
	v1.create_order(
		supabase_id=order,
		customer="c_nordlicht",
		po_no="NL-2026-117",
		transaction_date="2026-08-03",
		delivery_date="2026-10-30",
		company=company,
		items=[
			{
				"item": "i_wp12",
				"qty": 1,
				"description": "BV Elbblick, Haus B: Luft/Wasser-Wärmepumpe 12 kW inkl. 500-l-Pufferspeicher",
			},
			{
				"item": "i_fbh",
				"qty": 420,
				"description": "BV Elbblick, Haus B: Fußbodenheizung EG–2. OG, 14 Heizkreise",
			},
			{"item": "i_mont", "qty": 160},
			{"item": "i_hyd", "qty": 1},
		],
	)
	dp1 = _invoice(
		"dp-nordlicht-1",
		"c_nordlicht",
		"down_payment",
		order=order,
		percent=30,
		posting_date="2026-08-14",
		company=company,
	)
	dp2 = _invoice(
		"dp-nordlicht-2",
		"c_nordlicht",
		"down_payment",
		order=order,
		percent=30,
		posting_date="2026-09-15",
		company=company,
	)
	_invoice(
		"dp-nordlicht-3",
		"c_nordlicht",
		"down_payment",
		order=order,
		percent=20,
		posting_date="2026-10-12",
		company=company,
	)
	_pay(dp1, None, "2026-08-21", "Gutschrift Nordlicht 21.08.2026")
	_pay(dp2, 12000.00, "2026-09-29", "Gutschrift Nordlicht 29.09.2026 (Teilzahlung)")
	_invoice(
		"final-nordlicht",
		"c_nordlicht",
		"final",
		order=order,
		posting_date="2026-10-30",
		service_period="03.08.2026 – 30.10.2026",
		company=company,
	)


def project_musterhausen(company: str):
	"""Public-sector customer (Leitweg-ID): one paid down payment, final invoice still a draft."""
	order = "o_musterhausen_kita"
	v1.create_order(
		supabase_id=order,
		customer="c_musterhausen",
		po_no="Vergabe 2026/0815-HZ",
		transaction_date="2026-09-08",
		delivery_date="2026-11-02",
		company=company,
		items=[
			{
				"item": "i_bwt24",
				"qty": 2,
				"description": "Kita Sonnenblume: Kaskade aus zwei Gas-Brennwertthermen 24 kW",
			},
			{"item": "i_hk22", "qty": 18},
			{"item": "i_mont", "qty": 64},
			{"item": "i_hyd", "qty": 1},
		],
	)
	dp = _invoice(
		"dp-musterhausen-1",
		"c_musterhausen",
		"down_payment",
		order=order,
		percent=40,
		posting_date="2026-09-22",
		company=company,
	)
	_pay(dp, None, "2026-10-06", "Stadtkasse Musterhausen 06.10.2026")
	_invoice(
		"final-musterhausen",
		"c_musterhausen",
		"final",
		order=order,
		posting_date="2026-11-02",
		service_period="08.09.2026 – 30.10.2026",
		submit=False,
		company=company,
	)


def project_hansabau(company: str):
	"""Subcontracted construction work under § 13b UStG: no VAT, revenue on 8337."""
	order = "o_hansabau_billbrook"
	v1.create_order(
		supabase_id=order,
		customer="c_hansabau",
		po_no="HB-NU-2026-044",
		transaction_date="2026-09-01",
		delivery_date="2026-10-28",
		company=company,
		items=[
			{
				"item": "i_fbh",
				"qty": 260,
				"description": "BV Gewerbehof Billbrook: Fußbodenheizung Hallenbüros",
			},
			{"item": "i_mont", "qty": 40},
		],
	)
	dp = _invoice(
		"dp-hansabau-1",
		"c_hansabau",
		"down_payment",
		order=order,
		amount=10000,
		posting_date="2026-09-18",
		company=company,
	)
	_pay(dp, None, "2026-09-30", "Hansa Bau 30.09.2026")
	_invoice(
		"final-hansabau",
		"c_hansabau",
		"final",
		order=order,
		posting_date="2026-10-28",
		service_period="09/2026 – 10/2026",
		company=company,
	)


def standard_invoices(company: str):
	berger = _invoice(
		"re-berger-wartung",
		"c_berger",
		"standard",
		items=[
			{"item": "i_wart", "qty": 1},
			{"item": "i_mont", "qty": 1.5, "description": "Austausch Ausdehnungsgefäß"},
		],
		posting_date="2026-10-07",
		service_period="07.10.2026",
		company=company,
	)
	_pay(berger, None, "2026-10-14", "Berger Wartung")
	_invoice(
		"re-weber-heizkoerper",
		"c_weber",
		"standard",
		items=[{"item": "i_hk22", "qty": 3}, {"item": "i_mont", "qty": 6}],
		posting_date="2026-09-10",
		service_period="08.09.2026 – 09.09.2026",
		company=company,
	)
	_invoice(
		"re-vandijk-planung",
		"c_vandijk",
		"standard",
		items=[
			{"item": "i_plan", "qty": 12, "description": "Fachplanung Heizlast und Rohrnetz, Projekt Haarlem"}
		],
		posting_date="2026-10-20",
		due_days=30,
		service_period="10/2026",
		company=company,
	)
	_invoice(
		"re-berger-bad",
		"c_berger",
		"standard",
		items=[{"item": "i_vwi", "qty": 1}, {"item": "i_mont", "qty": 12}],
		posting_date="2026-10-26",
		service_period="19.10.2026 – 23.10.2026",
		company=company,
	)


def _invoice(key: str, customer: str, kind: str, **kwargs) -> str:
	return v1.create_invoice(
		idempotency_key=f"sample-{key}", customer=customer, kind=kind, supabase_id=f"inv_{key}", **kwargs
	)["name"]


def _pay(invoice: str, amount: float | None, posting_date: str, reference: str):
	if frappe.db.exists("Payment Entry", {"reference_no": reference, "docstatus": 1}):
		return
	outstanding = frappe.db.get_value("Sales Invoice", invoice, "outstanding_amount")
	v1.record_payment(
		invoice=invoice, amount=amount or outstanding, posting_date=posting_date, reference_no=reference
	)


def create_integration_user(email: str = "office-app@muster-haustechnik.de") -> dict:
	"""Dedicated API user for the office app (token auth)."""
	if not frappe.db.exists("User", email):
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": "Office App",
				"user_type": "System User",
				"send_welcome_email": 0,
				"roles": [{"role": role} for role in ("Accounts Manager", "Sales Manager", "Item Manager")],
			}
		).insert()
	else:
		user = frappe.get_doc("User", email)
	secret = generate_keys(user.name)["api_secret"]
	frappe.db.commit()
	return {"api_key": frappe.db.get_value("User", user.name, "api_key"), "api_secret": secret}
