# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, nowdate

from erpnext.accounts.general_ledger import make_gl_entries, make_reverse_gl_entries
from erpnext.accounts.utils import get_account_currency, update_voucher_outstanding


@frappe.whitelist()
def get_retention_release_details(
	reference_doctype: str, reference_name: str, retention_amount: float | None = None
):
	if reference_doctype not in ("Sales Invoice", "Purchase Invoice"):
		frappe.throw(_("Reference Document Type must be Sales Invoice or Purchase Invoice"))

	invoice = frappe.get_doc(reference_doctype, reference_name)
	if invoice.docstatus != 1:
		frappe.throw(_("Reference invoice must be submitted"))
	if not invoice.get("enable_retention"):
		frappe.throw(_("Retention is not enabled on reference invoice"))

	if reference_doctype == "Sales Invoice":
		party_type = "Customer"
		party = invoice.customer
		party_account = invoice.debit_to
		default_retention_account_field = "default_retention_receivable_account"
	else:
		party_type = "Supplier"
		party = invoice.supplier
		party_account = invoice.credit_to
		default_retention_account_field = "default_retention_payable_account"

	retention_account = invoice.retention_account or frappe.get_cached_value(
		"Company", invoice.company, default_retention_account_field
	)
	if not retention_account:
		frappe.throw(
			_("Retention Account is missing on reference invoice and Company default retention account")
		)

	release_amount = flt(retention_amount) or flt(invoice.retention_outstanding_amount)
	return frappe._dict(
		company=invoice.company,
		posting_date=nowdate(),
		party_type=party_type,
		party=party,
		project=invoice.project,
		party_account=party_account,
		retention_account=retention_account,
		party_account_currency=invoice.party_account_currency,
		retention_amount=release_amount,
		base_retention_amount=flt(
			release_amount * flt(invoice.conversion_rate),
			frappe.get_precision("Retention Release Entry", "base_retention_amount"),
		),
		retention_outstanding_amount=invoice.retention_outstanding_amount,
	)


class RetentionReleaseEntry(Document):
	def set_missing_values(self):
		if not self.posting_date:
			self.posting_date = nowdate()

		if not self.reference_doctype or not self.reference_name:
			return

		details = get_retention_release_details(
			self.reference_doctype, self.reference_name, self.retention_amount
		)
		if self.posting_date:
			details.posting_date = self.posting_date
		self.update(details)

	def validate(self):
		self.set_missing_values()
		self.validate_reference_invoice()

	def validate_reference_invoice(self):
		invoice = frappe.get_doc(self.reference_doctype, self.reference_name)
		if invoice.docstatus != 1:
			frappe.throw(_("Reference invoice must be submitted"))
		if not invoice.get("enable_retention"):
			frappe.throw(_("Retention is not enabled on reference invoice"))
		if not self.retention_account:
			frappe.throw(_("Retention Account is mandatory"))
		if not self.party_account:
			frappe.throw(_("Party Account is mandatory"))
		if flt(self.retention_amount) <= 0:
			frappe.throw(_("Retention Release Amount must be greater than zero"))
		if flt(self.retention_amount) > flt(invoice.retention_outstanding_amount):
			frappe.throw(_("Retention Release Amount cannot exceed outstanding retention amount"))

	def on_submit(self):
		self.make_gl_entries()
		self.update_invoice_retention_amounts()

	def on_cancel(self):
		make_reverse_gl_entries(voucher_type=self.doctype, voucher_no=self.name)
		self.update_invoice_retention_amounts(cancel=True)
		update_voucher_outstanding(
			self.reference_doctype,
			self.reference_name,
			self.party_account,
			self.party_type,
			self.party,
		)

	def make_gl_entries(self):
		party_account_currency = get_account_currency(self.party_account)
		retention_account_currency = get_account_currency(self.retention_account)
		company_currency = frappe.get_cached_value("Company", self.company, "default_currency")
		amount = flt(self.retention_amount)
		base_amount = flt(self.base_retention_amount)

		if self.reference_doctype == "Sales Invoice":
			party_entry = {
				"account": self.party_account,
				"party_type": self.party_type,
				"party": self.party,
				"against": self.retention_account,
				"debit": base_amount,
				"debit_in_account_currency": base_amount
				if party_account_currency == company_currency
				else amount,
				"debit_in_transaction_currency": amount,
				"against_voucher": self.reference_name,
				"against_voucher_type": self.reference_doctype,
			}
			retention_entry = {
				"account": self.retention_account,
				"party_type": self.party_type,
				"party": self.party,
				"against": self.party_account,
				"credit": base_amount,
				"credit_in_account_currency": base_amount
				if retention_account_currency == company_currency
				else amount,
				"credit_in_transaction_currency": amount,
				"against_voucher": self.reference_name,
				"against_voucher_type": self.reference_doctype,
			}
		else:
			retention_entry = {
				"account": self.retention_account,
				"party_type": self.party_type,
				"party": self.party,
				"against": self.party_account,
				"debit": base_amount,
				"debit_in_account_currency": base_amount
				if retention_account_currency == company_currency
				else amount,
				"debit_in_transaction_currency": amount,
				"against_voucher": self.reference_name,
				"against_voucher_type": self.reference_doctype,
			}
			party_entry = {
				"account": self.party_account,
				"party_type": self.party_type,
				"party": self.party,
				"against": self.retention_account,
				"credit": base_amount,
				"credit_in_account_currency": base_amount
				if party_account_currency == company_currency
				else amount,
				"credit_in_transaction_currency": amount,
				"against_voucher": self.reference_name,
				"against_voucher_type": self.reference_doctype,
			}

		common = {
			"posting_date": self.posting_date,
			"company": self.company,
			"voucher_type": self.doctype,
			"voucher_no": self.name,
			"project": self.project,
			"remarks": self.remarks,
		}
		gl_entries = []
		for entry, account_currency in (
			(party_entry, party_account_currency),
			(retention_entry, retention_account_currency),
		):
			entry.update(common)
			entry["account_currency"] = account_currency
			gl_entries.append(frappe._dict(entry))

		make_gl_entries(gl_entries, cancel=False, update_outstanding="No", merge_entries=False)
		update_voucher_outstanding(
			self.reference_doctype,
			self.reference_name,
			self.party_account,
			self.party_type,
			self.party,
		)

	def update_invoice_retention_amounts(self, cancel=False):
		invoice = frappe.get_doc(self.reference_doctype, self.reference_name)
		multiplier = -1 if cancel else 1
		released_amount = flt(invoice.retention_released_amount) + multiplier * flt(self.retention_amount)
		outstanding_amount = flt(invoice.retention_amount) - released_amount

		values = {
			"retention_released_amount": released_amount,
			"retention_outstanding_amount": outstanding_amount,
		}
		if self.reference_doctype == "Sales Invoice":
			values["net_receivable_amount"] = flt(invoice.grand_total) - outstanding_amount
		else:
			values["net_payable_amount"] = flt(invoice.grand_total) - outstanding_amount

		frappe.db.set_value(
			self.reference_doctype,
			self.reference_name,
			values,
			update_modified=False,
		)
