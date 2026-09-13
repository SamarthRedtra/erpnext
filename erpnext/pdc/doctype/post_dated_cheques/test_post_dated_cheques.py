# Copyright (c) 2026, ERPNext contributors
# See license.txt

import frappe
from frappe.utils import nowdate

from erpnext.accounts.doctype.purchase_invoice.test_purchase_invoice import make_purchase_invoice
from erpnext.accounts.doctype.sales_invoice.test_sales_invoice import create_sales_invoice
from erpnext.accounts.report.general_ledger.general_ledger import execute as execute_general_ledger
from erpnext.pdc.doctype.post_dated_cheques.post_dated_cheques import (
	bounce_post_dated_cheque,
	clear_post_dated_cheque,
	convert_post_dated_cheques,
	present_post_dated_cheque,
)
from erpnext.tests.utils import ERPNextTestSuite


class TestPostDatedCheques(ERPNextTestSuite):
	def make_pdc(self, sales_invoice=None, amount=None):
		sales_invoice = sales_invoice or create_sales_invoice(rate=1000)
		amount = amount or sales_invoice.outstanding_amount
		customer_name = frappe.db.get_value("Customer", sales_invoice.customer, "customer_name")
		pdc = frappe.get_doc(
			{
				"doctype": "Post Dated Cheques",
				"company": sales_invoice.company,
				"posting_date": nowdate(),
				"payment_type": "Receive",
				"party_type": "Customer",
				"party": sales_invoice.customer,
				"party_name": customer_name,
				"mode_of_payment": "Cash",
				"reference_no": frappe.generate_hash(length=8),
				"reference_date": nowdate(),
				"bank_account": "_Test Bank - _TC",
				"amount": amount,
				"invoice_references": [
					{
						"reference_doctype": "Sales Invoice",
						"reference_name": sales_invoice.name,
						"total_amount": sales_invoice.grand_total,
						"outstanding_amount": sales_invoice.outstanding_amount,
						"allocated_amount": amount,
					}
				],
			}
		)
		pdc.insert()
		pdc.submit()
		return pdc, sales_invoice

	def test_convert_post_dated_cheque_to_payment_entry(self):
		pdc, sales_invoice = self.make_pdc()

		result = convert_post_dated_cheques(
			rows=[{"pdc": pdc.name, "bank_account": "_Test Bank - _TC"}],
			defaults={"company": pdc.company},
		)

		self.assertFalse(result["failures"])
		self.assertEqual(len(result["created"]), 1)

		pdc.reload()
		self.assertEqual(pdc.status, "Cleared")
		self.assertTrue(pdc.payment_entry)

		payment_entry = frappe.get_doc("Payment Entry", pdc.payment_entry)
		self.assertEqual(payment_entry.docstatus, 1)
		self.assertEqual(payment_entry.is_pdc_entry, 1)
		self.assertEqual(payment_entry.post_dated_cheque, pdc.name)
		self.assertEqual(payment_entry.references[0].reference_name, sales_invoice.name)

	def test_present_clear_and_bounce_restore_outstanding(self):
		pdc, sales_invoice = self.make_pdc()
		original_outstanding = sales_invoice.outstanding_amount

		present_post_dated_cheque(pdc.name)
		pdc.reload()
		sales_invoice.reload()
		self.assertEqual(pdc.status, "Presented")
		self.assertEqual(sales_invoice.outstanding_amount, original_outstanding)

		cleared = clear_post_dated_cheque(pdc.name, "_Test Bank - _TC")
		pdc.reload()
		sales_invoice.reload()
		self.assertEqual(pdc.status, "Cleared")
		self.assertEqual(sales_invoice.outstanding_amount, 0)
		self.assertEqual(clear_post_dated_cheque(pdc.name)["payment_entry"], cleared["payment_entry"])

		bounce_post_dated_cheque(pdc.name)
		pdc.reload()
		sales_invoice.reload()
		self.assertEqual(pdc.status, "Bounced")
		self.assertEqual(frappe.db.get_value("Payment Entry", pdc.payment_entry, "docstatus"), 2)
		self.assertEqual(sales_invoice.outstanding_amount, original_outstanding)
		self.assertTrue(bounce_post_dated_cheque(pdc.name)["already_bounced"])

	def test_active_pdcs_cannot_over_allocate_invoice(self):
		sales_invoice = create_sales_invoice(rate=1000)
		available = sales_invoice.outstanding_amount
		self.make_pdc(sales_invoice, available * 0.6)
		self.make_pdc(sales_invoice, available * 0.4)

		with self.assertRaises(frappe.ValidationError):
			self.make_pdc(sales_invoice, 1)

	def test_outgoing_pdc_clears_purchase_invoice(self):
		purchase_invoice = make_purchase_invoice(rate=500)
		pdc = frappe.get_doc(
			{
				"doctype": "Post Dated Cheques",
				"company": purchase_invoice.company,
				"posting_date": nowdate(),
				"payment_type": "Pay",
				"party_type": "Supplier",
				"party": purchase_invoice.supplier,
				"party_name": purchase_invoice.supplier_name,
				"mode_of_payment": "Cash",
				"reference_no": frappe.generate_hash(length=8),
				"reference_date": nowdate(),
				"bank_account": "_Test Bank - _TC",
				"amount": purchase_invoice.outstanding_amount,
				"invoice_references": [
					{
						"reference_doctype": "Purchase Invoice",
						"reference_name": purchase_invoice.name,
						"allocated_amount": purchase_invoice.outstanding_amount,
					}
				],
			}
		).insert()
		pdc.submit()
		clear_post_dated_cheque(pdc.name, "_Test Bank - _TC")
		purchase_invoice.reload()
		self.assertEqual(purchase_invoice.outstanding_amount, 0)

	def test_pending_pdc_is_visible_in_general_ledger(self):
		pdc, sales_invoice = self.make_pdc()

		columns, data = execute_general_ledger(
			frappe._dict(
				{
					"company": pdc.company,
					"from_date": nowdate(),
					"to_date": nowdate(),
					"party_type": "Customer",
					"party": [sales_invoice.customer],
					"show_post_dated_cheques": 1,
					"show_remarks": 1,
					"categorize_by": "Categorize by Voucher (Consolidated)",
				}
			)
		)

		self.assertTrue(any(column.get("fieldname") == "pdc_amount" for column in columns))
		self.assertTrue(
			any(
				row.get("voucher_type") == "Post Dated Cheques" and row.get("voucher_no") == pdc.name
				for row in data
			)
		)
