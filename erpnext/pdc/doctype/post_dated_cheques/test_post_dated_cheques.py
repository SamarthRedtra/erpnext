# Copyright (c) 2026, ERPNext contributors
# See license.txt

import frappe
from frappe.utils import nowdate

from erpnext.accounts.doctype.sales_invoice.test_sales_invoice import create_sales_invoice
from erpnext.accounts.report.general_ledger.general_ledger import execute as execute_general_ledger
from erpnext.pdc.doctype.post_dated_cheques.post_dated_cheques import convert_post_dated_cheques
from erpnext.tests.utils import ERPNextTestSuite


class TestPostDatedCheques(ERPNextTestSuite):
	def make_pdc(self):
		sales_invoice = create_sales_invoice(rate=1000)
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
				"amount": sales_invoice.outstanding_amount,
				"invoice_references": [
					{
						"reference_doctype": "Sales Invoice",
						"reference_name": sales_invoice.name,
						"total_amount": sales_invoice.grand_total,
						"outstanding_amount": sales_invoice.outstanding_amount,
						"allocated_amount": sales_invoice.outstanding_amount,
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

		self.assertFalse(result.failures)
		self.assertEqual(len(result.created), 1)

		pdc.reload()
		self.assertEqual(pdc.status, "Converted")
		self.assertTrue(pdc.payment_entry)

		payment_entry = frappe.get_doc("Payment Entry", pdc.payment_entry)
		self.assertEqual(payment_entry.docstatus, 1)
		self.assertEqual(payment_entry.is_pdc_entry, 1)
		self.assertEqual(payment_entry.post_dated_cheque, pdc.name)
		self.assertEqual(payment_entry.references[0].reference_name, sales_invoice.name)

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
			any(row.get("voucher_type") == "Post Dated Cheques" and row.get("voucher_no") == pdc.name for row in data)
		)
