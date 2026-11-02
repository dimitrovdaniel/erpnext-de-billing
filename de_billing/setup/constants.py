"""German (SKR03) accounting constants used by setup and invoicing."""

# Accounts the SKR03 chart shipped with ERPNext does not contain, but German invoicing needs.
# (account number, account name, parent group in the ERPNext SKR03 chart, account type)
EXTRA_ACCOUNTS = [
	(
		"1710",
		"Erhaltene Anzahlungen (Verbindlichkeiten)",
		"III. Erhaltene Anzahlungen auf Bestellungen",
		"",
	),
	(
		"1711",
		"Erhaltene, versteuerte Anzahlungen 7 % USt (Verbindlichkeiten)",
		"III. Erhaltene Anzahlungen auf Bestellungen",
		"",
	),
	(
		"1718",
		"Erhaltene, versteuerte Anzahlungen 19 % USt (Verbindlichkeiten)",
		"III. Erhaltene Anzahlungen auf Bestellungen",
		"",
	),
	(
		"8125",
		"Steuerfreie innergemeinschaftliche Lieferungen nach § 4 Nr. 1b UStG",
		"Erlöskonten 8",
		"Income Account",
	),
	(
		"8337",
		"Erlöse aus Leistungen, für die der Leistungsempfänger die Steuer nach § 13b UStG schuldet",
		"Erlöskonten 8",
		"Income Account",
	),
	(
		"8338",
		"Erlöse aus im Inland nicht steuerbaren sonstigen Leistungen im übrigen Gemeinschaftsgebiet",
		"Erlöskonten 8",
		"Income Account",
	),
]

# Output VAT accounts and their rate.
OUTPUT_VAT_ACCOUNTS = {"1776": 19, "1771": 7}

# Received down payments are booked as a liability until the final invoice, by VAT rate.
DOWN_PAYMENT_ACCOUNTS = {19: "1718", 7: "1711", 0: "1710"}

# Tax treatment (as used by the API) -> (Tax Category, sales tax template created by ERPNext's German setup)
TAX_TREATMENTS = {
	"domestic": ("Inland", "Lieferung oder sonstige Leistung im Inland"),
	"reverse_charge_13b": ("§ 13b UStG", "Bauleistungen nach § 13b UStG"),
	"eu_goods": ("EU-Lieferung", "Lieferung an Unternehmen in der EU"),
	"eu_services": ("EU-Leistung", "Sonstige Leistung an Unternehmen in der EU"),
}

# Revenue account per tax category; domestic revenue is split by VAT rate.
REVENUE_ACCOUNTS = {
	"Inland": {19: "8400", 7: "8300", 0: "8200"},
	"§ 13b UStG": "8337",
	"EU-Lieferung": "8125",
	"EU-Leistung": "8338",
}

# Item tax templates created by ERPNext's German setup ("<rate> % - <abbr>").
ITEM_TAX_TEMPLATE_TITLES = {19: "19 %", 7: "7 %", 0: "0 %"}

# Number series per invoice kind (Rechnung, Abschlagsrechnung, Schlussrechnung, Rechnungskorrektur).
NAMING_SERIES = {
	"Standard": "RE-.YYYY.-",
	"Down Payment Invoice": "AR-.YYYY.-",
	"Final Invoice": "SR-.YYYY.-",
}
CORRECTION_SERIES = "RK-.YYYY.-"
ORDER_SERIES = "AB-.YYYY.-"

DOWN_PAYMENT_ITEM = "ABSCHLAG"
DOWN_PAYMENT_ITEM_GROUP = "Anzahlungen"

CUSTOMER_GROUPS = {
	"Business Customer": "Gewerbekunden",
	"Private Customer": "Privatkunden",
	"Public Authority": "Öffentliche Auftraggeber",
}

UOMS = ["Stk", "Std.", "m²", "Pauschal", "lfm"]
