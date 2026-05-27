# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import frappe
from frappe import _
from frappe.utils import flt


def set_advance_recovery_defaults_from_project(doc):
	if not doc.get("project"):
		return

	project = frappe.get_cached_value(
		"Project",
		doc.project,
		["enable_advance_recovery", "advance_recovery_percentage"],
		as_dict=True,
	)
	if not project or not project.enable_advance_recovery:
		return

	doc.enable_advance_recovery = 1
	doc.advance_recovery_percentage = project.advance_recovery_percentage
	doc.allocate_advances_automatically = 1


def validate_advance_recovery(doc):
	if doc.doctype not in ("Sales Invoice", "Purchase Invoice"):
		return

	if doc.get("project"):
		set_advance_recovery_defaults_from_project(doc)

	if not doc.get("enable_advance_recovery"):
		doc.advance_recovery_percentage = 0
		doc.advance_recovery_amount = 0
		return

	if flt(doc.advance_recovery_percentage) <= 0 or flt(doc.advance_recovery_percentage) > 100:
		frappe.throw(_("Advance Recovery % must be greater than 0 and less than or equal to 100"))

	doc.advance_recovery_amount = get_advance_recovery_limit(doc)


def get_advance_recovery_limit(doc):
	if not doc.get("enable_advance_recovery") or not flt(doc.get("advance_recovery_percentage")):
		return None

	if doc.get("party_account_currency") == doc.company_currency:
		invoice_amount = flt(doc.get("base_rounded_total") or doc.base_grand_total)
	else:
		invoice_amount = flt(doc.get("rounded_total") or doc.grand_total)

	return flt(
		invoice_amount * flt(doc.advance_recovery_percentage) / 100,
		doc.precision("advance_recovery_amount"),
	)
