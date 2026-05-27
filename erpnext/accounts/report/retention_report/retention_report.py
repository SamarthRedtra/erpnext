# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
	filters = frappe._dict(filters or {})
	columns = get_columns()
	data = get_data(filters)
	return columns, data


def get_columns():
	return [
		{"label": _("Invoice Type"), "fieldname": "invoice_type", "fieldtype": "Data", "width": 140},
		{
			"label": _("Invoice"),
			"fieldname": "invoice",
			"fieldtype": "Dynamic Link",
			"options": "invoice_type",
			"width": 180,
		},
		{"label": _("Posting Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 100},
		{"label": _("Company"), "fieldname": "company", "fieldtype": "Link", "options": "Company"},
		{"label": _("Project"), "fieldname": "project", "fieldtype": "Link", "options": "Project"},
		{"label": _("Party Type"), "fieldname": "party_type", "fieldtype": "Data", "width": 100},
		{
			"label": _("Party"),
			"fieldname": "party",
			"fieldtype": "Dynamic Link",
			"options": "party_type",
			"width": 180,
		},
		{"label": _("Grand Total"), "fieldname": "grand_total", "fieldtype": "Currency"},
		{"label": _("Retention Amount"), "fieldname": "retention_amount", "fieldtype": "Currency"},
		{"label": _("Retention Released"), "fieldname": "retention_released_amount", "fieldtype": "Currency"},
		{
			"label": _("Retention Outstanding"),
			"fieldname": "retention_outstanding_amount",
			"fieldtype": "Currency",
		},
		{"label": _("Amount Due Now"), "fieldname": "amount_due_now", "fieldtype": "Currency"},
		{"label": _("Release Date"), "fieldname": "retention_release_date", "fieldtype": "Date"},
		{"label": _("Retention Account"), "fieldname": "retention_account", "fieldtype": "Link", "options": "Account"},
		{"label": _("Status"), "fieldname": "retention_status", "fieldtype": "Data"},
		{"label": _("Release Retention"), "fieldname": "release_retention", "fieldtype": "Data"},
	]


def get_data(filters):
	data = []
	if filters.get("invoice_type") in (None, "", "Sales Invoice"):
		data.extend(get_invoice_data("Sales Invoice", filters))
	if filters.get("invoice_type") in (None, "", "Purchase Invoice"):
		data.extend(get_invoice_data("Purchase Invoice", filters))
	return data


def get_invoice_data(doctype, filters):
	if doctype == "Sales Invoice":
		party_type = "Customer"
		party_field = "customer"
		amount_due_field = "net_receivable_amount"
	else:
		party_type = "Supplier"
		party_field = "supplier"
		amount_due_field = "net_payable_amount"

	conditions = ["docstatus = 1", "enable_retention = 1", "retention_amount > 0"]
	values = {}
	for field in ("company", "project"):
		if filters.get(field):
			conditions.append(f"{field} = %({field})s")
			values[field] = filters.get(field)

	if filters.get("party"):
		conditions.append(f"{party_field} = %(party)s")
		values["party"] = filters.party
	if filters.get("from_date"):
		conditions.append("posting_date >= %(from_date)s")
		values["from_date"] = filters.from_date
	if filters.get("to_date"):
		conditions.append("posting_date <= %(to_date)s")
		values["to_date"] = filters.to_date
	if filters.get("release_date"):
		conditions.append("retention_release_date <= %(release_date)s")
		values["release_date"] = filters.release_date

	rows = frappe.db.sql(
		f"""
		select
			name as invoice,
			posting_date,
			company,
			project,
			{party_field} as party,
			grand_total,
			retention_amount,
			retention_released_amount,
			retention_outstanding_amount,
			{amount_due_field} as amount_due_now,
			retention_release_date,
			retention_account
		from `tab{doctype}`
		where {" and ".join(conditions)}
		order by posting_date desc, name desc
		""",
		values,
		as_dict=True,
	)

	for row in rows:
		row.invoice_type = doctype
		row.party_type = party_type
		row.retention_status = "Released" if flt(row.retention_outstanding_amount) == 0 else "Outstanding"
		row.release_retention = "Release Retention" if flt(row.retention_outstanding_amount) > 0 else ""

	return rows
