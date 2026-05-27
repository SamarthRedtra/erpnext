# Copyright (c) 2026, ERPNext contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate, today

from erpnext.accounts.doctype.payment_entry.payment_entry import get_account_details
from erpnext.accounts.party import get_party_account


class PostDatedCheques(Document):
	def validate(self):
		self.set_invoice_links()
		self.validate_invoice_references_not_reused()

	def before_cancel(self):
		"""Cancel submitted Payment Entries linked to this PDC."""
		payment_entries = self._get_linked_payment_entries()
		for payment_entry_name in payment_entries:
			pe = frappe.get_doc("Payment Entry", payment_entry_name)
			if pe.docstatus == 1:
				pe.cancel()

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

	def validate_invoice_references_not_reused(self):
		"""Prevent one active invoice from being issued on multiple PDCs."""
		for row in self.get("invoice_references") or []:
			if not row.reference_doctype or not row.reference_name:
				continue

			existing = get_existing_pdc_for_invoice(
				row.reference_doctype,
				row.reference_name,
				exclude_pdc=self.name if not self.is_new() else None,
			)
			if existing:
				frappe.throw(
					_("{0} {1} is already linked to active PDC {2}.").format(
						_(row.reference_doctype),
						frappe.bold(row.reference_name),
						frappe.bold(existing),
					),
					title=_("Invoice Already Used in PDC"),
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
			"docstatus": ["!=", 2],
			"status": ["!=", "Cancelled"],
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

	query = """
		SELECT
			name,
			COALESCE(bill_no, '') AS bill_no,
			grand_total,
			outstanding_amount
		FROM `tabPurchase Invoice`
		WHERE docstatus = 1
			AND company = %(company)s
			AND supplier = %(supplier)s
			AND outstanding_amount > 0
	"""
	values = {"company": company, "supplier": supplier}

	if search_txt:
		query += """
			AND (
				name LIKE %(txt)s
				OR bill_no LIKE %(txt)s
			)
		"""
		values["txt"] = f"%{search_txt}%"

	query += """
		ORDER BY posting_date DESC, name DESC
		LIMIT %(start)s, %(page_len)s
	"""
	values["start"] = start
	values["page_len"] = page_len

	return frappe.db.sql(query, values, as_dict=True)


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
	supplier_invoice_expr = "COALESCE(inv.bill_no, '')"
	search_txt = (txt or "").strip()
	start = int(start or 0)
	page_len = int(page_len or 20)

	values = {
		"company": company,
		"party": party,
		"current_pdc": current_pdc or "",
		"start": start,
		"page_len": page_len,
	}

	query = f"""
		SELECT
			inv.name,
			{supplier_invoice_expr if reference_doctype == "Purchase Invoice" else "''"} AS bill_no,
			inv.grand_total,
			inv.outstanding_amount
		FROM `tab{reference_doctype}` inv
		WHERE inv.docstatus = 1
			AND inv.company = %(company)s
			AND inv.{party_field} = %(party)s
			AND inv.outstanding_amount > 0
			AND NOT EXISTS (
				SELECT 1
				FROM `tabPDC Invoice Reference` ref
				INNER JOIN `tabPost Dated Cheques` pdc ON pdc.name = ref.parent
				WHERE ref.reference_doctype = %(reference_doctype)s
					AND ref.reference_name = inv.name
					AND pdc.docstatus != 2
					AND IFNULL(pdc.status, '') != 'Cancelled'
					AND (%(current_pdc)s = '' OR pdc.name != %(current_pdc)s)
			)
	"""
	values["reference_doctype"] = reference_doctype

	if search_txt:
		if reference_doctype == "Purchase Invoice":
			query += """
				AND (
					inv.name LIKE %(txt)s
					OR inv.bill_no LIKE %(txt)s
				)
			"""
		else:
			query += " AND inv.name LIKE %(txt)s"
		values["txt"] = f"%{search_txt}%"

	query += """
		ORDER BY inv.posting_date DESC, inv.name DESC
		LIMIT %(start)s, %(page_len)s
	"""

	return frappe.db.sql(query, values, as_dict=True)


@frappe.whitelist()
def get_pdc_invoice_details(reference_doctype, invoices, current_pdc=None):
	if isinstance(invoices, str):
		invoices = frappe.parse_json(invoices)
	invoices = list(dict.fromkeys(invoices or []))

	if reference_doctype not in ("Sales Invoice", "Purchase Invoice") or not invoices:
		return []

	for invoice in invoices:
		existing = get_existing_pdc_for_invoice(
			reference_doctype,
			invoice,
			exclude_pdc=current_pdc,
		)
		if existing:
			frappe.throw(
				_("{0} {1} is already linked to active PDC {2}.").format(
					_(reference_doctype),
					frappe.bold(invoice),
					frappe.bold(existing),
				),
				title=_("Invoice Already Used in PDC"),
			)

	fields = ["name", "grand_total", "outstanding_amount"]
	if reference_doctype == "Purchase Invoice":
		fields.append("bill_no")

	return frappe.get_all(
		reference_doctype,
		filters={
			"name": ["in", invoices],
			"docstatus": 1,
			"outstanding_amount": [">", 0],
		},
		fields=fields,
		order_by="posting_date desc, name desc",
	)


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
		frappe.throw(_("At least one invoice reference is required to convert {0}").format(frappe.bold(pdc.name)))

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
		"status": "Pending",
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

	return frappe.get_all(
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


@frappe.whitelist()
def convert_post_dated_cheques(rows, defaults=None):
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
			pdc = frappe.get_doc("Post Dated Cheques", pdc_name)
			pdc.check_permission("read")

			if pdc.payment_entry and frappe.db.exists("Payment Entry", pdc.payment_entry):
				created.append(
					{"pdc": pdc.name, "payment_entry": pdc.payment_entry, "already_converted": True}
				)
				continue

			if pdc.docstatus != 1 or pdc.status != "Pending":
				frappe.throw(_("Only submitted Pending PDCs can be converted"))

			bank_account = row.get("bank_account") or defaults.get("default_bank_account") or pdc.bank_account
			posting_date = row.get("posting_date_override") or defaults.get("posting_date") or today()
			payment_entry = make_payment_entry_from_pdc(pdc, bank_account, posting_date)
			payment_entry.insert()
			payment_entry.submit()

			frappe.db.set_value(
				"Post Dated Cheques",
				pdc.name,
				{
					"payment_entry": payment_entry.name,
					"actual_posting_date": payment_entry.posting_date,
					"status": "Converted",
				},
				update_modified=False,
			)
			created.append({"pdc": pdc.name, "payment_entry": payment_entry.name})
		except Exception:
			failures.append({"pdc": pdc_name, "error": frappe.get_traceback()})
			frappe.db.rollback(save_point=savepoint)

	return {"created": created, "failures": failures}


def get_pdc_gl_entries(filters):
	"""Return non-posting rows so pending PDCs are visible in GL/SOA output."""
	query_filters = {
		"company": filters.get("company"),
		"docstatus": 1,
		"status": "Pending",
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

	pdc_rows = frappe.get_all(
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
				voucher_subtype=_("Pending PDC"),
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
				remarks=_("Pending PDC {0}, Cheque/Reference No {1}, Amount {2}").format(
					pdc.name, pdc.reference_no, pdc.amount
				),
			)
		)

	return entries


