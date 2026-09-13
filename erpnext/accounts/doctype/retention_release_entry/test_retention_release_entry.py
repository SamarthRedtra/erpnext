# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import frappe

from erpnext.accounts.doctype.account.test_account import create_account
from erpnext.accounts.doctype.payment_entry.test_payment_entry import create_payment_entry
from erpnext.accounts.doctype.purchase_invoice.test_purchase_invoice import make_purchase_invoice
from erpnext.accounts.doctype.sales_invoice.test_sales_invoice import create_sales_invoice
from erpnext.projects.doctype.project.test_project import make_project
from erpnext.tests.utils import ERPNextTestSuite


class TestRetentionReleaseEntry(ERPNextTestSuite):
	def setUp(self):
		self.receivable = create_account(
			parent_account="Accounts Receivable - _TC",
			account_name=f"Retention Receivable {frappe.generate_hash(length=5)}",
			company="_Test Company",
			account_type="Receivable",
		)
		self.payable = create_account(
			parent_account="Accounts Payable - _TC",
			account_name=f"Retention Payable {frappe.generate_hash(length=5)}",
			company="_Test Company",
			account_type="Payable",
		)
		frappe.db.set_value(
			"Company",
			"_Test Company",
			{
				"default_retention_receivable_account": self.receivable,
				"default_retention_payable_account": self.payable,
			},
		)
		self.project = make_project(
			{"project_name": f"Retention VAT {frappe.generate_hash(length=6)}", "company": "_Test Company"}
		)
		self.project.enable_retention = 1
		self.project.retention_percentage = 10
		self.project.enable_advance_recovery = 1
		self.project.advance_recovery_percentage = 20
		self.project.save()

	def add_five_percent_tax(self, invoice):
		invoice.append(
			"taxes",
			{
				"charge_type": "On Net Total",
				"account_head": "_Test Account VAT - _TC",
				"description": "VAT 5%",
				"rate": 5,
			},
		)

	def make_customer_advance(self, amount):
		return create_payment_entry(
			company="_Test Company",
			payment_type="Receive",
			party_type="Customer",
			party="_Test Customer",
			paid_from="Debtors - _TC",
			paid_to="_Test Bank - _TC",
			paid_amount=amount,
			save=True,
			submit=True,
		)

	def make_supplier_advance(self, amount):
		return create_payment_entry(
			company="_Test Company",
			payment_type="Pay",
			party_type="Supplier",
			party="_Test Supplier",
			paid_from="_Test Bank - _TC",
			paid_to="Creditors - _TC",
			paid_amount=amount,
			save=True,
			submit=True,
		)

	def test_sales_retention_and_advance_use_vat_inclusive_total(self):
		advance = self.make_customer_advance(500)
		invoice = create_sales_invoice(rate=1000, do_not_save=True)
		invoice.project = self.project.name
		self.add_five_percent_tax(invoice)
		invoice.save()
		invoice.submit()

		self.assertEqual(invoice.grand_total, 1050)
		self.assertEqual(invoice.retention_amount, 105)
		self.assertEqual(invoice.advance_recovery_amount, 210)
		self.assertEqual(invoice.total_advance, 210)
		self.assertEqual(invoice.outstanding_amount, 735)
		advance.reload()
		self.assertEqual(advance.unallocated_amount, 290)

	def test_purchase_retention_and_advance_use_vat_inclusive_total(self):
		advance = self.make_supplier_advance(500)
		invoice = make_purchase_invoice(qty=1, rate=1000, do_not_save=True)
		invoice.project = self.project.name
		self.add_five_percent_tax(invoice)
		invoice.save()
		invoice.submit()

		self.assertEqual(invoice.grand_total, 1050)
		self.assertEqual(invoice.retention_amount, 105)
		self.assertEqual(invoice.advance_recovery_amount, 210)
		self.assertEqual(invoice.total_advance, 210)
		self.assertEqual(invoice.outstanding_amount, 735)
		advance.reload()
		self.assertEqual(advance.unallocated_amount, 290)

	def test_partial_full_release_and_cancellation(self):
		invoice = create_sales_invoice(rate=1000, do_not_save=True)
		invoice.project = self.project.name
		self.add_five_percent_tax(invoice)
		invoice.save()
		invoice.submit()

		partial = frappe.get_doc(
			{
				"doctype": "Retention Release Entry",
				"reference_doctype": "Sales Invoice",
				"reference_name": invoice.name,
				"retention_amount": 40,
			}
		).insert()
		partial.submit()
		invoice.reload()
		self.assertEqual(invoice.retention_released_amount, 40)
		self.assertEqual(invoice.retention_outstanding_amount, 65)

		partial.cancel()
		invoice.reload()
		self.assertEqual(invoice.retention_released_amount, 0)
		self.assertEqual(invoice.retention_outstanding_amount, 105)

		full = frappe.get_doc(
			{
				"doctype": "Retention Release Entry",
				"reference_doctype": "Sales Invoice",
				"reference_name": invoice.name,
				"retention_amount": 105,
			}
		).insert()
		full.submit()
		invoice.reload()
		self.assertEqual(invoice.retention_released_amount, 105)
		self.assertEqual(invoice.retention_outstanding_amount, 0)

		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": "Retention Release Entry",
					"reference_doctype": "Sales Invoice",
					"reference_name": invoice.name,
					"retention_amount": 1,
				}
			).insert()
