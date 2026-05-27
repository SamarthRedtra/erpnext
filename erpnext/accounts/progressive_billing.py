# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import frappe
from frappe.utils import flt


PROGRESSIVE_BILLING_CONFIG = {
	"Sales Invoice": {
		"child_table": "Sales Invoice Item",
		"references": (
			("so_detail", "Sales Order Item", "amount"),
			("dn_detail", "Delivery Note Item", "amount"),
		),
	},
	"Purchase Invoice": {
		"child_table": "Purchase Invoice Item",
		"references": (
			("po_detail", "Purchase Order Item", "amount"),
			("pr_detail", "Purchase Receipt Item", "amount"),
		),
	},
}


def set_progressive_billing_values(doc):
	config = PROGRESSIVE_BILLING_CONFIG.get(doc.doctype)
	if not config:
		return

	for row in doc.get("items"):
		current_qty = flt(row.qty, row.precision("current_billed_qty"))
		current_amount = flt(row.amount, row.precision("current_billed_amount"))
		previous_qty, previous_amount, reference_amount = get_previous_billing_values(
			doc, row, config
		)

		row.previous_billed_qty = previous_qty
		row.previous_billed_amount = previous_amount
		row.current_billed_qty = current_qty
		row.current_billed_amount = current_amount
		row.accumulated_billed_qty = flt(
			previous_qty + current_qty, row.precision("accumulated_billed_qty")
		)
		row.accumulated_billed_amount = flt(
			previous_amount + current_amount, row.precision("accumulated_billed_amount")
		)
		row.billing_percentage = (
			flt(row.accumulated_billed_amount / reference_amount * 100, row.precision("billing_percentage"))
			if reference_amount
			else 0
		)


def get_previous_billing_values(doc, row, config):
	for fieldname, reference_doctype, reference_amount_field in config["references"]:
		reference_row = row.get(fieldname)
		if not reference_row:
			continue

		previous_qty, previous_amount = get_previous_billing_by_reference(
			config["child_table"], fieldname, reference_row, doc.name
		)
		reference_amount = flt(
			frappe.db.get_value(reference_doctype, reference_row, reference_amount_field)
		)
		return previous_qty, previous_amount, reference_amount

	if row.get("project") and row.get("item_code"):
		previous_qty, previous_amount = get_previous_billing_by_project_item(
			config["child_table"], row.project, row.item_code, doc.name
		)
		return previous_qty, previous_amount, 0

	return 0, 0, 0


def get_previous_billing_by_reference(child_table, reference_field, reference_row, current_parent):
	return get_previous_billing(
		child_table,
		f"{reference_field} = %(reference_row)s",
		{"reference_row": reference_row},
		current_parent,
	)


def get_previous_billing_by_project_item(child_table, project, item_code, current_parent):
	return get_previous_billing(
		child_table,
		"project = %(project)s and item_code = %(item_code)s",
		{"project": project, "item_code": item_code},
		current_parent,
	)


def get_previous_billing(child_table, condition, values, current_parent):
	values = dict(values)
	values["current_parent"] = current_parent

	result = frappe.db.sql(
		f"""
		select
			sum(qty) as previous_qty,
			sum(amount) as previous_amount
		from `tab{child_table}`
		where
			docstatus = 1
			and parent != %(current_parent)s
			and {condition}
		""",
		values,
		as_dict=True,
	)

	if not result:
		return 0, 0

	return flt(result[0].previous_qty), flt(result[0].previous_amount)
