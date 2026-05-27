// Copyright (c) 2026, ERPNext contributors
// For license information, please see license.txt

/**
 * Frappe Link: if `link_filters` is any non-empty string (including "[]"), link.js treats it
 * as active and overwrites get_query on every search — frm.set_query then never wins.
 * Do not set link_filters in JSON; clear any leftover on the field df + register query here.
 */
function clear_bank_account_link_filters(frm) {
	const df = frappe.meta.get_docfield("Post Dated Cheques", "bank_account");
	if (df) {
		df.link_filters = null;
	}
	if (frm.fields_dict.bank_account && frm.fields_dict.bank_account.df) {
		frm.fields_dict.bank_account.df.link_filters = null;
	}
}

function set_bank_account_query(frm) {
	clear_bank_account_link_filters(frm);
	frm.set_query("bank_account", function () {
		if (!frm.doc.company) {
			return { filters: { name: ["in", []] } };
		}
		return {
			filters: {
				company: frm.doc.company,
				is_group: 0,
				account_type: ["in", ["Bank", "Cash"]],
			},
		};
	});
}

function get_pdc_invoice_doctype(frm) {
	return frm.doc.party_type === "Supplier" || frm.doc.payment_type === "Pay"
		? "Purchase Invoice"
		: "Sales Invoice";
}

function render_invoice_links(frm) {
	const wrapper = frm.fields_dict.invoice_links_html?.$wrapper;
	const display = frm.fields_dict.invoice_links?.$wrapper;

	const links = (frm.doc.invoice_links || "")
		.split(",")
		.map(v => v.trim())
		.filter(Boolean);

	if (!links.length) {
		if (wrapper) {
			wrapper.html("");
		}
		return;
	}

	const invoice_doctype = get_pdc_invoice_doctype(frm);
	const invoice_route = frappe.router.slug(invoice_doctype);
	const inline_html = links.map(name => {
		const route = `/app/${invoice_route}/${encodeURIComponent(name)}`;
		return `<a href="${route}">${frappe.utils.escape_html(name)}</a>`;
	}).join(", ");
	const button_html = links.map(name => {
		const route = `/app/${invoice_route}/${encodeURIComponent(name)}`;
		return `<a class="btn btn-xs btn-default" style="margin: 0 6px 6px 0;" href="${route}">
			<i class="fa fa-external-link"></i> ${frappe.utils.escape_html(name)}
		</a>`;
	}).join("");

	if (display) {
		display.find(".control-value").html(inline_html);
	}
	if (wrapper) {
		wrapper.html(`<div class="pdc-invoice-links">${button_html}</div>`);
	}
}

frappe.ui.form.on("Post Dated Cheques", {
	setup(frm) {
		set_bank_account_query(frm);
	},
	onload(frm) {
		set_bank_account_query(frm);
	},
	refresh(frm) {
		set_bank_account_query(frm);
		render_invoice_links(frm);

		if (frm.doc.docstatus === 0) {
			frm.add_custom_button(__("Fetch Invoices"), () => {
				frm.events.fetch_invoices(frm);
			});
		}

		if (frm.doc.docstatus === 1 && frm.doc.status === "Pending") {
			frm.add_custom_button(__("Convert Cheque"), () => {
				frappe.prompt([
					{
						label: __("Execution Date"),
						fieldname: "execution_date",
						fieldtype: "Date",
						default: frm.doc.reference_date,
						reqd: 1
					}
				], (values) => {
					frappe.call({
						method: "erpnext.pdc.doctype.post_dated_cheques.post_dated_cheques.convert_post_dated_cheques",
						args: {
							rows: [{
								pdc: frm.doc.name,
								bank_account: frm.doc.bank_account,
								posting_date_override: values.execution_date
							}],
							defaults: {
								company: frm.doc.company
							}
						},
						callback: function(r) {
							if (r.message && r.message.created && r.message.created.length) {
								const pe_name = r.message.created[0].payment_entry;
								frappe.show_alert({
									message: __("Converted to Payment Entry: {0}", [
										`<a href="/app/payment-entry/${pe_name}">${pe_name}</a>`
									]),
									indicator: "green"
								});
								frm.reload_doc();
							}
							if (r.message && r.message.failures && r.message.failures.length) {
								frappe.msgprint({
									title: __("Conversion Failed"),
									message: r.message.failures[0].error,
									indicator: "red"
								});
							}
						}
					});
				}, __("Convert to Payment Entry"), __("Convert"));
			});
		}

		if (frm.is_new()) return;

		if (frm.doc.payment_entry) {
			frm.add_custom_button(
				__("Open Payment Entry"),
				() => frappe.set_route("Form", "Payment Entry", frm.doc.payment_entry),
				__("Payment")
			);
			frm.add_custom_button(
				__("General Ledger"),
				() => {
					frappe.route_options = {
						company: frm.doc.company,
						voucher_no: frm.doc.payment_entry,
						from_date: frm.doc.reference_date,
						to_date: frm.doc.reference_date,
					};
					frappe.set_route("query-report", "General Ledger");
				},
				__("Payment")
			);
		}
	},
	company(frm) {
		frm.set_value("bank_account", "");
		set_bank_account_query(frm);
		frm.clear_table("invoice_references");
		calculate_total_amount(frm);
	},
	payment_type(frm) {
		if (frm.doc.payment_type === "Receive") {
			frm.set_value("party_type", "Customer");
		} else if (frm.doc.payment_type === "Pay") {
			frm.set_value("party_type", "Supplier");
		}
	},
	party_type(frm) {
		frm.set_value("party", "");
		frm.set_value("party_name", "");
		frm.clear_table("invoice_references");
		calculate_total_amount(frm);
	},
	party(frm) {
		if (frm.doc.party && frm.doc.party_type) {
			const name_field = frm.doc.party_type === "Customer" ? "customer_name" : "supplier_name";
			frappe.db.get_value(frm.doc.party_type, frm.doc.party, name_field, (r) => {
				if (r && r[name_field]) {
					frm.set_value("party_name", r[name_field]);
				}
			});
		} else {
			frm.set_value("party_name", "");
		}
		frm.clear_table("invoice_references");
		calculate_total_amount(frm);
	},

	fetch_invoices(frm) {
		if (!frm.doc.party) {
			frappe.msgprint(__("Please select a Party first."));
			return;
		}

		const is_purchase_invoice = frm.doc.party_type !== "Customer";
		const target_doctype = is_purchase_invoice ? "Purchase Invoice" : "Sales Invoice";
		const party_field = frm.doc.party_type === "Customer" ? "customer" : "supplier";
		const invoice_query_filters = {
			reference_doctype: target_doctype,
			company: frm.doc.company,
			party: frm.doc.party,
			current_pdc: frm.doc.name
		};

		const dialog = new frappe.ui.form.MultiSelectDialog({
			doctype: target_doctype,
			target: frm,
			columns: is_purchase_invoice
				? ["name", "bill_no", "grand_total", "outstanding_amount"]
				: undefined,
			setters: [
				{
					fieldname: "company",
					label: __("Company"),
					fieldtype: "Link",
					options: "Company",
					default: frm.doc.company,
				},
				{
					fieldname: party_field,
					label: __(frm.doc.party_type),
					fieldtype: "Link",
					options: frm.doc.party_type,
					default: frm.doc.party,
				}
			],
			get_query() {
				return {
					query: "erpnext.pdc.doctype.post_dated_cheques.post_dated_cheques.search_invoice_for_pdc",
					filters: invoice_query_filters
				};
			},
			action(selections) {
				if (!selections.length) return;

				frappe.call({
					method: "erpnext.pdc.doctype.post_dated_cheques.post_dated_cheques.get_pdc_invoice_details",
					args: {
						reference_doctype: target_doctype,
						invoices: selections,
						current_pdc: frm.doc.name
					},
					callback(r) {
						if (r.message) {
							r.message.forEach(d => {
								const row = frm.add_child("invoice_references");
								row.reference_doctype = target_doctype;
								row.reference_name = d.name;
								row.total_amount = d.grand_total;
								row.outstanding_amount = d.outstanding_amount;
								row.allocated_amount = d.outstanding_amount;
							});

							calculate_total_amount(frm);
							frm.refresh_field("invoice_references");
						}
					}
				});
				dialog.dialog.hide();
			}
		});
	}
});

frappe.ui.form.on("PDC Invoice Reference", {
	allocated_amount(frm, cdt, cdn) {
		calculate_total_amount(frm);
	},
	invoice_references_remove(frm, cdt, cdn) {
		calculate_total_amount(frm);
	},
	reference_name(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (row.reference_name) {
			frappe.db.get_value(row.reference_doctype, row.reference_name, ["grand_total", "outstanding_amount"], (r) => {
				if (r) {
					frappe.model.set_value(cdt, cdn, "total_amount", r.grand_total);
					frappe.model.set_value(cdt, cdn, "outstanding_amount", r.outstanding_amount);
					frappe.model.set_value(cdt, cdn, "allocated_amount", r.outstanding_amount);
					calculate_total_amount(frm);
				}
			});
		}
	}
});

function calculate_total_amount(frm) {
	let total = 0;
	(frm.doc.invoice_references || []).forEach(row => {
		total += flt(row.allocated_amount);
	});
	frm.set_value("amount", total);
}
