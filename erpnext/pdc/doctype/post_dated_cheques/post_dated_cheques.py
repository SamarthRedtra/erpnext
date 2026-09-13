# Copyright (c) 2026, ERPNext contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate, today

from erpnext.accounts.doctype.payment_entry.payment_entry import get_account_details
from erpnext.accounts.party import get_party_account

ACTIVE_PDC_STATUSES = ("Pending", "Presented")
SUPPORTED_INVOICE_TYPES = ("Sales Invoice", "Purchase Invoice")


class PostDatedCheques(Document):
	def validate(self):
		self.set_invoice_links()
		self.validate_party_and_invoice_references()

	def before_cancel(self):
		"""Cancel submitted Payment Entries linked to this PDC."""
		self.cancel_linked_payment_entries()

	def on_cancel(self):
		self.status = "Cancelled"
		self.db_update()

	def before_insert(self):
		self.status = "Pending"

	def set_invoice_links(self):
		"""Store linked invoice IDs on the parent so they are visible/searchable in list view."""
		references = []
		seen = set()
		for row in self.get("invoice_references") or []:
			if not row.reference_doctype or not row.reference_name:
				continue
			key = (row.reference_doctype, row.reference_name)
			if key in seen:
				continue
			seen.add(key)
			references.append(row.reference_name)
		self.invoice_links = ", ".join(references)
		summary = references[:3]
		if len(references) > 3:
			summary.append(_("+{0} more").format(len(references) - 3))
		self.invoice_links_list = ", ".join(summary)

	def validate_party_and_invoice_references(self):
		expected_party_type, expected_invoice_type = get_expected_party_and_invoice_type(self.payment_type)
		if self.party_type != expected_party_type:
			frappe.throw(
				_("Payment Type {0} requires Party Type {1}.").format(
					frappe.bold(self.payment_type), frappe.bold(expected_party_type)
				)
			)

		if not self.get("invoice_references"):
			frappe.throw(_("At least one invoice reference is required."))

		total_allocated = 0
		seen = set()
		for row in self.invoice_references:
			if row.reference_doctype != expected_invoice_type:
				frappe.throw(
					_("Row {0}: {1} PDCs can only reference {2}.").format(
						row.idx, self.payment_type, expected_invoice_type
					)
				)
			if not row.reference_name:
				frappe.throw(_("Row {0}: Invoice is required.").format(row.idx))
			key = (row.reference_doctype, row.reference_name)
			if key in seen:
				frappe.throw(_("Row {0}: Invoice {1} is duplicated.").format(row.idx, row.reference_name))
			seen.add(key)

			allocated_amount = flt(row.allocated_amount)
			if allocated_amount <= 0:
				frappe.throw(_("Row {0}: Allocated Amount must be greater than zero.").format(row.idx))

			invoice = get_invoice_for_pdc(row.reference_doctype, row.reference_name, for_update=True)
			if invoice.company != self.company:
				frappe.throw(_("Row {0}: Invoice company does not match the PDC company.").format(row.idx))
			if invoice.party != self.party:
				frappe.throw(_("Row {0}: Invoice party does not match the PDC party.").format(row.idx))

			reserved = get_active_pdc_allocated_amount(
				row.reference_doctype,
				row.reference_name,
				exclude_pdc=self.name if not self.is_new() else None,
			)
			available = max(flt(invoice.outstanding_amount) - reserved, 0)
			if allocated_amount > available:
				frappe.throw(
					_("Row {0}: Allocated Amount {1} exceeds the available invoice balance {2}.").format(
						row.idx,
						frappe.format_value(allocated_amount, "Currency"),
						frappe.format_value(available, "Currency"),
					)
				)

			row.total_amount = invoice.grand_total
			row.outstanding_amount = invoice.outstanding_amount
			total_allocated += allocated_amount

		if flt(self.amount) <= 0:
			frappe.throw(_("PDC Amount must be greater than zero."))
		if abs(flt(self.amount) - total_allocated) > 0.005:
			frappe.throw(
				_("PDC Amount {0} must equal the total invoice allocation {1}.").format(
					frappe.format_value(self.amount, "Currency"),
					frappe.format_value(total_allocated, "Currency"),
				)
			)

	def _get_linked_payment_entries(self):
		linked = set()

		if self.payment_entry:
			linked.add(self.payment_entry)

		# Fallback: include any PDC-generated entries matching this cheque details.
		rows = frappe.get_all(
			"Payment Entry",
			filters={
				"docstatus": ["in", [0, 1]],
				"is_pdc_entry": 1,
				"post_dated_cheque": self.name,
				"company": self.company,
				"party_type": self.party_type,
				"party": self.party,
				"reference_no": self.reference_no,
			},
			pluck="name",
		)
		linked.update(rows or [])
		return list(linked)

	def cancel_linked_payment_entries(self):
		"""Cancel generated entries without the PDC's audit link blocking cancellation."""
		payment_entries = self._get_linked_payment_entries()
		primary_payment_entry = self.payment_entry
		if primary_payment_entry:
			frappe.db.set_value("Post Dated Cheques", self.name, "payment_entry", None, update_modified=False)
		try:
			for payment_entry_name in payment_entries:
				payment_entry = frappe.get_doc("Payment Entry", payment_entry_name)
				if payment_entry.docstatus == 1:
					payment_entry.cancel()
		finally:
			if primary_payment_entry:
				frappe.db.set_value(
					"Post Dated Cheques",
					self.name,
					"payment_entry",
					primary_payment_entry,
					update_modified=False,
				)


def get_expected_party_and_invoice_type(payment_type):
	if payment_type == "Receive":
		return "Customer", "Sales Invoice"
	if payment_type == "Pay":
		return "Supplier", "Purchase Invoice"
	frappe.throw(_("Payment Type must be Receive or Pay."))


def get_invoice_for_pdc(reference_doctype, reference_name, for_update=False):
	if reference_doctype not in SUPPORTED_INVOICE_TYPES:
		frappe.throw(_("Unsupported invoice type {0}.").format(reference_doctype))
	if not frappe.has_permission(reference_doctype, "read", reference_name):
		frappe.throw(
			_("You are not permitted to read {0} {1}.").format(reference_doctype, reference_name),
			frappe.PermissionError,
		)

	lock_clause = " FOR UPDATE" if for_update else ""
	party_field = "customer" if reference_doctype == "Sales Invoice" else "supplier"
	rows = frappe.db.sql(
		f"""
			SELECT name, company, {party_field} AS party, grand_total, outstanding_amount, docstatus
			FROM `tab{reference_doctype}`
			WHERE name = %s{lock_clause}
		""",
		(reference_name,),
		as_dict=True,
	)
	if not rows or rows[0].docstatus != 1:
		frappe.throw(_("Submitted {0} {1} was not found.").format(reference_doctype, reference_name))
	return rows[0]


def get_active_pdc_allocated_amount(reference_doctype, reference_name, exclude_pdc=None):
	conditions = ""
	values = {
		"reference_doctype": reference_doctype,
		"reference_name": reference_name,
		"statuses": ACTIVE_PDC_STATUSES,
	}
	if exclude_pdc:
		conditions = " AND pdc.name != %(exclude_pdc)s"
		values["exclude_pdc"] = exclude_pdc

	return flt(
		frappe.db.sql(
			f"""
				SELECT COALESCE(SUM(ref.allocated_amount), 0)
				FROM `tabPDC Invoice Reference` ref
				INNER JOIN `tabPost Dated Cheques` pdc ON pdc.name = ref.parent
				WHERE ref.reference_doctype = %(reference_doctype)s
					AND ref.reference_name = %(reference_name)s
					AND pdc.docstatus = 1
					AND pdc.status IN %(statuses)s
					{conditions}
			""",
			values,
		)[0][0]
	)


def get_existing_pdc_for_invoice(reference_doctype, reference_name, exclude_pdc=None):
	filters = {
		"reference_doctype": reference_doctype,
		"reference_name": reference_name,
	}
	if exclude_pdc:
		filters["parent"] = ["!=", exclude_pdc]

	rows = frappe.get_all(
		"PDC Invoice Reference",
		filters=filters,
		fields=["parent"],
		order_by="modified desc",
	)
	if not rows:
		return None

	parents = [row.parent for row in rows]
	active = frappe.get_all(
		"Post Dated Cheques",
		filters={
			"name": ["in", parents],
			"docstatus": 1,
			"status": ["in", ACTIVE_PDC_STATUSES],
		},
		pluck="name",
		limit=1,
	)
	return active[0] if active else None


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def search_purchase_invoice_for_pdc(doctype, txt, searchfield, start, page_len, filters):
	"""Search Purchase Invoice with Supplier Invoice No for PDC fetch dialog."""
	if isinstance(filters, str):
		filters = frappe.parse_json(filters)
	if not filters:
		filters = {}

	company = filters.get("company")
	supplier = filters.get("supplier")

	if not company or not supplier:
		return []

	page_len = int(page_len) if page_len else 20
	start = int(start) if start else 0
	search_txt = (txt or "").strip()

	query_filters = {
		"docstatus": 1,
		"company": company,
		"supplier": supplier,
		"outstanding_amount": [">", 0],
	}
	or_filters = None
	if search_txt:
		or_filters = {
			"name": ["like", f"%{search_txt}%"],
			"bill_no": ["like", f"%{search_txt}%"],
		}

	return frappe.get_list(
		"Purchase Invoice",
		filters=query_filters,
		or_filters=or_filters,
		fields=["name", "bill_no", "grand_total", "outstanding_amount"],
		order_by="posting_date desc, name desc",
		start=start,
		page_length=page_len,
	)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def search_invoice_for_pdc(doctype, txt, searchfield, start, page_len, filters):
	if isinstance(filters, str):
		filters = frappe.parse_json(filters)
	if not filters:
		filters = {}

	reference_doctype = filters.get("reference_doctype") or doctype
	company = filters.get("company")
	party = filters.get("party")
	current_pdc = filters.get("current_pdc")

	if reference_doctype not in ("Sales Invoice", "Purchase Invoice"):
		return []
	if not company or not party:
		return []

	party_field = "customer" if reference_doctype == "Sales Invoice" else "supplier"
	search_txt = (txt or "").strip()
	start = int(start or 0)
	page_len = int(page_len or 20)
	query_filters = {
		"company": company,
		party_field: party,
		"docstatus": 1,
		"outstanding_amount": [">", 0],
	}
	or_filters = None
	if search_txt:
		if reference_doctype == "Purchase Invoice":
			or_filters = {
				"name": ["like", f"%{search_txt}%"],
				"bill_no": ["like", f"%{search_txt}%"],
			}
		else:
			query_filters["name"] = ["like", f"%{search_txt}%"]

	fields = ["name", "grand_total", "outstanding_amount"]
	if reference_doctype == "Purchase Invoice":
		fields.append("bill_no")
	rows = frappe.get_list(
		reference_doctype,
		filters=query_filters,
		or_filters=or_filters,
		fields=fields,
		order_by="posting_date desc, name desc",
		start=start,
		page_length=page_len,
	)
	for row in rows:
		row.bill_no = row.get("bill_no") or ""
		row.available_pdc_amount = max(
			flt(row.outstanding_amount)
			- get_active_pdc_allocated_amount(reference_doctype, row.name, exclude_pdc=current_pdc),
			0,
		)
	return [row for row in rows if row.available_pdc_amount > 0]


@frappe.whitelist()
def get_pdc_invoice_details(reference_doctype, invoices, current_pdc=None):
	if isinstance(invoices, str):
		invoices = frappe.parse_json(invoices)
	invoices = list(dict.fromkeys(invoices or []))

	if reference_doctype not in ("Sales Invoice", "Purchase Invoice") or not invoices:
		return []

	fields = ["name", "grand_total", "outstanding_amount"]
	if reference_doctype == "Purchase Invoice":
		fields.append("bill_no")

	rows = frappe.get_list(
		reference_doctype,
		filters={
			"name": ["in", invoices],
			"docstatus": 1,
			"outstanding_amount": [">", 0],
		},
		fields=fields,
		order_by="posting_date desc, name desc",
	)
	for row in rows:
		row.available_pdc_amount = max(
			flt(row.outstanding_amount)
			- get_active_pdc_allocated_amount(reference_doctype, row.name, exclude_pdc=current_pdc),
			0,
		)
	return [row for row in rows if row.available_pdc_amount > 0]


@frappe.whitelist()
def list_available_invoice_balances(reference_doctype, company, party, current_pdc=None):
	if reference_doctype not in SUPPORTED_INVOICE_TYPES:
		frappe.throw(_("Reference Type must be Sales Invoice or Purchase Invoice."))

	party_field = "customer" if reference_doctype == "Sales Invoice" else "supplier"
	rows = frappe.get_list(
		reference_doctype,
		filters={
			"company": company,
			party_field: party,
			"docstatus": 1,
			"outstanding_amount": [">", 0],
		},
		fields=["name", "posting_date", "due_date", "grand_total", "outstanding_amount"],
		order_by="posting_date asc, name asc",
	)
	for row in rows:
		row.active_pdc_allocated = get_active_pdc_allocated_amount(
			reference_doctype, row.name, exclude_pdc=current_pdc
		)
		row.available_pdc_amount = max(flt(row.outstanding_amount) - row.active_pdc_allocated, 0)
	return [row for row in rows if row.available_pdc_amount > 0]


def get_invoice_party_account(pdc):
	for row in pdc.get("invoice_references") or []:
		if not row.reference_doctype or not row.reference_name:
			continue

		invoice = frappe.get_cached_doc(row.reference_doctype, row.reference_name)
		return invoice.debit_to if row.reference_doctype == "Sales Invoice" else invoice.credit_to

	return get_party_account(pdc.party_type, pdc.party, pdc.company)


def set_payment_entry_accounts(payment_entry, pdc, bank_account):
	party_account = get_invoice_party_account(pdc)
	party_account_details = get_account_details(party_account, payment_entry.posting_date, pdc.cost_center)
	bank_account_details = get_account_details(bank_account, payment_entry.posting_date, pdc.cost_center)

	if pdc.payment_type == "Receive":
		payment_entry.paid_from = party_account
		payment_entry.paid_to = bank_account
		payment_entry.paid_from_account_type = party_account_details.account_type
		payment_entry.paid_from_account_currency = party_account_details.account_currency
		payment_entry.paid_to_account_type = bank_account_details.account_type
		payment_entry.paid_to_account_currency = bank_account_details.account_currency
	else:
		payment_entry.paid_from = bank_account
		payment_entry.paid_to = party_account
		payment_entry.paid_from_account_type = bank_account_details.account_type
		payment_entry.paid_from_account_currency = bank_account_details.account_currency
		payment_entry.paid_to_account_type = party_account_details.account_type
		payment_entry.paid_to_account_currency = party_account_details.account_currency


def append_payment_entry_references(payment_entry, pdc):
	for row in pdc.get("invoice_references") or []:
		if not row.reference_doctype or not row.reference_name:
			continue

		payment_entry.append(
			"references",
			{
				"reference_doctype": row.reference_doctype,
				"reference_name": row.reference_name,
				"total_amount": row.total_amount,
				"outstanding_amount": row.outstanding_amount,
				"allocated_amount": row.allocated_amount,
			},
		)


def make_payment_entry_from_pdc(pdc, bank_account, posting_date):
	if not bank_account:
		frappe.throw(_("Bank Account is mandatory for converting {0}").format(frappe.bold(pdc.name)))
	if not pdc.get("invoice_references"):
		frappe.throw(
			_("At least one invoice reference is required to convert {0}").format(frappe.bold(pdc.name))
		)

	payment_entry = frappe.new_doc("Payment Entry")
	payment_entry.payment_type = pdc.payment_type
	payment_entry.company = pdc.company
	payment_entry.posting_date = getdate(posting_date or today())
	payment_entry.mode_of_payment = pdc.mode_of_payment
	payment_entry.party_type = pdc.party_type
	payment_entry.party = pdc.party
	payment_entry.party_name = pdc.party_name
	payment_entry.reference_no = pdc.reference_no
	payment_entry.reference_date = pdc.reference_date
	payment_entry.project = pdc.project
	payment_entry.cost_center = pdc.cost_center
	payment_entry.is_pdc_entry = 1
	payment_entry.post_dated_cheque = pdc.name

	set_payment_entry_accounts(payment_entry, pdc, bank_account)
	append_payment_entry_references(payment_entry, pdc)

	payment_entry.setup_party_account_field()
	payment_entry.set_missing_values()
	payment_entry.set_missing_ref_details()

	amount = flt(pdc.amount)
	if pdc.payment_type == "Receive":
		payment_entry.paid_amount = amount
		payment_entry.received_amount = amount
	else:
		payment_entry.paid_amount = amount
		payment_entry.received_amount = amount

	payment_entry.set_amounts()
	return payment_entry


@frappe.whitelist()
def get_pending_post_dated_cheques(filters=None):
	if isinstance(filters, str):
		filters = frappe.parse_json(filters)
	filters = frappe._dict(filters or {})

	query_filters = {
		"docstatus": 1,
		"status": ["in", ACTIVE_PDC_STATUSES],
	}
	for field in ("name", "company", "cost_center", "department", "account_currency"):
		filter_value = filters.get("currency") if field == "account_currency" else filters.get(field)
		if filter_value:
			query_filters[field] = filter_value

	if filters.get("from_date"):
		query_filters["reference_date"] = [">=", filters.from_date]
	if filters.get("to_date"):
		existing_reference_date_filter = query_filters.get("reference_date")
		if existing_reference_date_filter:
			query_filters["reference_date"] = ["between", [filters.from_date, filters.to_date]]
		else:
			query_filters["reference_date"] = ["<=", filters.to_date]

	return frappe.get_list(
		"Post Dated Cheques",
		filters=query_filters,
		fields=[
			"name",
			"mode_of_payment",
			"payment_type",
			"reference_date",
			"amount",
			"party_type",
			"party",
			"party_name",
			"account_currency",
			"bank_account",
			"payment_entry",
			"status",
		],
		order_by="reference_date asc, name asc",
	)


def get_locked_pdc(pdc_name):
	if not frappe.db.exists("Post Dated Cheques", pdc_name):
		frappe.throw(_("Post Dated Cheque {0} was not found.").format(pdc_name))
	frappe.db.sql("SELECT name FROM `tabPost Dated Cheques` WHERE name = %s FOR UPDATE", pdc_name)
	return frappe.get_doc("Post Dated Cheques", pdc_name)


@frappe.whitelist()
def present_post_dated_cheque(pdc):
	doc = get_locked_pdc(pdc)
	doc.check_permission("write")
	if doc.docstatus != 1:
		frappe.throw(_("Only submitted PDCs can be presented."))
	if doc.status == "Presented":
		return {"pdc": doc.name, "status": doc.status, "already_presented": True}
	if doc.status != "Pending":
		frappe.throw(_("Only Pending PDCs can be presented."))

	presented_on = today()
	frappe.db.set_value(
		"Post Dated Cheques",
		doc.name,
		{"status": "Presented", "presented_on": presented_on},
		update_modified=False,
	)
	return {"pdc": doc.name, "status": "Presented", "presented_on": presented_on}


@frappe.whitelist()
def clear_post_dated_cheque(pdc, bank_account=None, posting_date=None):
	doc = get_locked_pdc(pdc)
	doc.check_permission("write")
	if doc.docstatus != 1:
		frappe.throw(_("Only submitted PDCs can be cleared."))

	if doc.payment_entry and frappe.db.exists("Payment Entry", doc.payment_entry):
		payment_entry = frappe.get_doc("Payment Entry", doc.payment_entry)
		if payment_entry.docstatus == 1:
			if doc.status != "Cleared":
				frappe.db.set_value(
					"Post Dated Cheques", doc.name, "status", "Cleared", update_modified=False
				)
			return {
				"pdc": doc.name,
				"payment_entry": payment_entry.name,
				"status": "Cleared",
				"already_cleared": True,
			}
		if payment_entry.docstatus == 2:
			frappe.throw(
				_("Linked Payment Entry {0} is cancelled; bounce or amend this PDC.").format(
					payment_entry.name
				)
			)

	if doc.status not in ACTIVE_PDC_STATUSES:
		frappe.throw(_("Only Pending or Presented PDCs can be cleared."))

	doc.validate_party_and_invoice_references()
	bank_account = bank_account or doc.bank_account
	payment_entry = make_payment_entry_from_pdc(doc, bank_account, posting_date or today())
	payment_entry.insert()
	payment_entry.submit()

	frappe.db.set_value(
		"Post Dated Cheques",
		doc.name,
		{
			"payment_entry": payment_entry.name,
			"actual_posting_date": payment_entry.posting_date,
			"status": "Cleared",
		},
		update_modified=False,
	)
	return {"pdc": doc.name, "payment_entry": payment_entry.name, "status": "Cleared"}


@frappe.whitelist()
def bounce_post_dated_cheque(pdc):
	doc = get_locked_pdc(pdc)
	doc.check_permission("write")
	if doc.docstatus != 1:
		frappe.throw(_("Only submitted PDCs can be bounced."))
	if doc.status == "Bounced":
		return {"pdc": doc.name, "status": doc.status, "already_bounced": True}
	if doc.status == "Cancelled":
		frappe.throw(_("Cancelled PDCs cannot be bounced."))

	doc.cancel_linked_payment_entries()

	frappe.db.set_value("Post Dated Cheques", doc.name, "status", "Bounced", update_modified=False)
	return {"pdc": doc.name, "payment_entry": doc.payment_entry, "status": "Bounced"}


@frappe.whitelist()
def cancel_post_dated_cheque(pdc):
	doc = get_locked_pdc(pdc)
	doc.check_permission("cancel")
	if doc.docstatus == 2:
		return {"pdc": doc.name, "status": "Cancelled", "already_cancelled": True}
	if doc.docstatus != 1:
		frappe.throw(_("Only submitted PDCs can be cancelled."))
	doc.cancel()
	return {"pdc": doc.name, "status": "Cancelled"}


@frappe.whitelist()
def convert_post_dated_cheques(rows, defaults=None):
	"""Compatibility alias: clear one or more PDCs using the legacy batch payload."""
	if isinstance(rows, str):
		rows = frappe.parse_json(rows)
	if isinstance(defaults, str):
		defaults = frappe.parse_json(defaults)
	defaults = frappe._dict(defaults or {})

	created = []
	failures = []
	for idx, row in enumerate(rows or []):
		row = frappe._dict(row)
		pdc_name = row.get("pdc")
		if not pdc_name:
			continue

		savepoint = f"convert_pdc_{idx}"
		frappe.db.savepoint(savepoint)
		try:
			result = clear_post_dated_cheque(
				pdc_name,
				bank_account=row.get("bank_account") or defaults.get("default_bank_account"),
				posting_date=row.get("posting_date_override") or defaults.get("posting_date"),
			)
			result["already_converted"] = result.pop("already_cleared", False)
			created.append(result)
		except Exception:
			failures.append({"pdc": pdc_name, "error": frappe.get_traceback()})
			frappe.db.rollback(save_point=savepoint)

	return {"created": created, "failures": failures}


def get_pdc_gl_entries(filters):
	"""Return non-posting rows so pending PDCs are visible in GL/SOA output."""
	query_filters = {
		"company": filters.get("company"),
		"docstatus": 1,
		"status": ["in", ACTIVE_PDC_STATUSES],
	}
	if filters.get("from_date") and filters.get("to_date"):
		query_filters["reference_date"] = ["between", [filters.from_date, filters.to_date]]
	elif filters.get("from_date"):
		query_filters["reference_date"] = [">=", filters.from_date]
	elif filters.get("to_date"):
		query_filters["reference_date"] = ["<=", filters.to_date]

	if filters.get("party_type"):
		query_filters["party_type"] = filters.party_type
	if filters.get("party"):
		query_filters["party"] = ["in", filters.party]
	if filters.get("project"):
		query_filters["project"] = ["in", filters.project]
	if filters.get("cost_center"):
		query_filters["cost_center"] = ["in", filters.cost_center]

	pdc_rows = frappe.get_list(
		"Post Dated Cheques",
		filters=query_filters,
		fields=[
			"name",
			"company",
			"reference_date",
			"party_type",
			"party",
			"party_name",
			"payment_type",
			"amount",
			"account_currency",
			"bank_account",
			"project",
			"cost_center",
			"reference_no",
			"status",
			"creation",
		],
		order_by="reference_date asc, name asc",
	)

	entries = []
	account_filter = set(filters.get("account") or [])
	for pdc in pdc_rows:
		account = get_invoice_party_account(frappe.get_doc("Post Dated Cheques", pdc.name))
		if account_filter and account not in account_filter and pdc.bank_account not in account_filter:
			continue

		entries.append(
			frappe._dict(
				gl_entry=None,
				posting_date=pdc.reference_date,
				account=account,
				party_type=pdc.party_type,
				party=pdc.party,
				party_name=pdc.party_name,
				voucher_type="Post Dated Cheques",
				voucher_subtype=_("{0} PDC (Non-posting)").format(pdc.status),
				voucher_no=pdc.name,
				cost_center=pdc.cost_center,
				project=pdc.project,
				against_voucher_type=None,
				against_voucher=None,
				account_currency=pdc.account_currency,
				against=pdc.bank_account,
				is_opening="No",
				creation=pdc.creation,
				debit=0,
				credit=0,
				debit_in_account_currency=0,
				credit_in_account_currency=0,
				debit_in_transaction_currency=None,
				credit_in_transaction_currency=None,
				transaction_currency=pdc.account_currency,
				pdc_amount=pdc.amount,
				remarks=_("{0} PDC {1} (non-posting), Cheque/Reference No {2}, Amount {3}").format(
					pdc.status, pdc.name, pdc.reference_no, pdc.amount
				),
			)
		)

	return entries
