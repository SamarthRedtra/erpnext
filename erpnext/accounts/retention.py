# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import frappe
from frappe import _
from frappe.utils import add_days, flt, getdate

from erpnext.accounts.utils import get_account_currency


def set_retention_defaults_from_project(doc):
	if not doc.get("project"):
		return

	project = frappe.get_cached_value(
		"Project",
		doc.project,
		["enable_retention", "retention_percentage", "retention_release_date", "retention_release_after_days"],
		as_dict=True,
	)
	if not project or not project.enable_retention:
		return

	doc.enable_retention = 1
	doc.retention_percentage = project.retention_percentage
	if project.retention_release_date:
		doc.retention_release_date = project.retention_release_date
	elif project.retention_release_after_days:
		posting_date = doc.get("bill_date") or doc.get("posting_date")
		if posting_date:
			doc.retention_release_date = add_days(getdate(posting_date), project.retention_release_after_days)

	if not doc.get("retention_account"):
		doc.retention_account = get_default_retention_account(doc)


def get_default_retention_account(doc):
	fieldname = (
		"default_retention_receivable_account"
		if doc.doctype == "Sales Invoice"
		else "default_retention_payable_account"
	)
	return frappe.get_cached_value("Company", doc.company, fieldname)


def calculate_retention(doc):
	if doc.doctype not in ("Sales Invoice", "Purchase Invoice"):
		return

	if doc.get("project"):
		set_retention_defaults_from_project(doc)

	if not doc.get("enable_retention"):
		clear_retention(doc)
		return

	if flt(doc.retention_percentage) <= 0 or flt(doc.retention_percentage) >= 100:
		frappe.throw(_("Retention % must be greater than 0 and less than 100"))

	total = flt(doc.get("rounded_total") or doc.get("grand_total"), doc.precision("grand_total"))
	base_total = flt(
		doc.get("base_rounded_total") or doc.get("base_grand_total"),
		doc.precision("base_grand_total"),
	)

	retention_amount = flt(total * flt(doc.retention_percentage) / 100, doc.precision("retention_amount"))
	base_retention_amount = flt(
		base_total * flt(doc.retention_percentage) / 100,
		doc.precision("base_retention_amount"),
	)

	doc.retention_amount = retention_amount
	doc.base_retention_amount = base_retention_amount
	doc.retention_released_amount = flt(
		doc.get("retention_released_amount"), doc.precision("retention_released_amount")
	)
	doc.retention_outstanding_amount = flt(
		retention_amount - doc.retention_released_amount,
		doc.precision("retention_outstanding_amount"),
	)

	net_amount = flt(total - doc.retention_outstanding_amount, doc.precision("retention_amount"))
	if doc.doctype == "Sales Invoice":
		doc.net_receivable_amount = net_amount
	else:
		doc.net_payable_amount = net_amount

	if doc.retention_amount and not doc.get("retention_account"):
		doc.retention_account = get_default_retention_account(doc)


def clear_retention(doc):
	doc.retention_percentage = 0
	doc.retention_amount = 0
	doc.base_retention_amount = 0
	doc.retention_released_amount = 0
	doc.retention_outstanding_amount = 0
	doc.retention_release_date = None
	doc.retention_account = None
	if doc.doctype == "Sales Invoice":
		doc.net_receivable_amount = 0
	else:
		doc.net_payable_amount = 0


def validate_retention(doc):
	calculate_retention(doc)
	if not doc.get("enable_retention") or not flt(doc.get("retention_amount")):
		return

	if not doc.get("retention_account"):
		frappe.throw(_("Retention Account is mandatory when retention is enabled"))

	account_company, is_group = frappe.get_cached_value(
		"Account", doc.retention_account, ["company", "is_group"]
	)
	if account_company != doc.company:
		frappe.throw(_("Retention Account must belong to company {0}").format(doc.company))
	if is_group:
		frappe.throw(_("Retention Account cannot be a group account"))


def get_party_retention_amounts(doc):
	retention_amount = flt(doc.get("retention_amount"))
	base_retention_amount = flt(doc.get("base_retention_amount"))

	if not doc.get("enable_retention") or not retention_amount:
		return 0, 0

	account_currency_amount = (
		base_retention_amount if doc.party_account_currency == doc.company_currency else retention_amount
	)
	return retention_amount, base_retention_amount, account_currency_amount


def append_retention_gl_entry(doc, gl_entries):
	if not doc.get("enable_retention") or not flt(doc.get("base_retention_amount")):
		return

	validate_retention(doc)

	retention_amount, base_retention_amount, account_currency_amount = get_party_retention_amounts(doc)
	account_currency = get_account_currency(doc.retention_account)

	if doc.doctype == "Sales Invoice":
		party_type = "Customer"
		party = doc.customer
		against = doc.against_income_account
		dr_or_cr = {
			"debit": base_retention_amount,
			"debit_in_account_currency": (
				base_retention_amount if account_currency == doc.company_currency else account_currency_amount
			),
			"debit_in_transaction_currency": retention_amount,
		}
	else:
		party_type = "Supplier"
		party = doc.supplier
		against = doc.against_expense_account
		dr_or_cr = {
			"credit": base_retention_amount,
			"credit_in_account_currency": (
				base_retention_amount if account_currency == doc.company_currency else account_currency_amount
			),
			"credit_in_transaction_currency": retention_amount,
		}

	against_voucher = doc.name
	if doc.is_return and doc.return_against and not doc.update_outstanding_for_self:
		against_voucher = doc.return_against

	gl_entries.append(
		doc.get_gl_dict(
			{
				"account": doc.retention_account,
				"party_type": party_type,
				"party": party,
				"due_date": doc.retention_release_date or doc.due_date,
				"against": against,
				"against_voucher": against_voucher,
				"against_voucher_type": doc.doctype,
				"project": doc.project,
				"cost_center": doc.cost_center,
				**dr_or_cr,
			},
			account_currency,
			item=doc,
		)
	)
