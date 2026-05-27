frappe.ui.form.on("Retention Release Entry", {
	setup(frm) {
		frm.set_query("reference_name", () => {
			if (!frm.doc.reference_doctype) {
				return {};
			}

			return {
				filters: {
					company: frm.doc.company,
					docstatus: 1,
					enable_retention: 1,
				},
			};
		});

		frm.set_query("party_account", () => {
			return {
				filters: {
					company: frm.doc.company,
					is_group: 0,
				},
			};
		});

		frm.set_query("retention_account", () => {
			return {
				filters: {
					company: frm.doc.company,
					is_group: 0,
				},
			};
		});
	},

	onload(frm) {
		frm.retention_release_fetching = false;
		if (frm.doc.reference_doctype && frm.doc.reference_name) {
			frappe.after_ajax(() => frm.events.fetch_retention_release_details(frm, true));
		}
	},

	refresh(frm) {
		if (frm.doc.docstatus > 0) {
			frm.add_custom_button(
				__("Ledger"),
				() => {
					frappe.route_options = {
						voucher_no: frm.doc.name,
						from_date: frm.doc.posting_date,
						to_date: moment(frm.doc.modified).format("YYYY-MM-DD"),
						company: frm.doc.company,
						categorize_by: "",
						show_cancelled_entries: frm.doc.docstatus === 2,
					};
					frappe.set_route("query-report", "General Ledger");
				},
				__("View")
			);
		}

		if (
			frm.is_new() &&
			frm.doc.reference_doctype &&
			frm.doc.reference_name &&
			(!frm.doc.retention_amount || !frm.doc.party_account || !frm.doc.retention_account)
		) {
			frm.events.fetch_retention_release_details(frm, true);
		}
	},

	reference_doctype(frm) {
		frm.set_value("reference_name", "");
	},

	reference_name(frm) {
		frm.events.fetch_retention_release_details(frm, true);
	},

	retention_amount(frm) {
		if (
			frm.doc.reference_doctype &&
			frm.doc.reference_name &&
			!frm.retention_release_fetching
		) {
			frm.events.fetch_retention_release_details(frm, false);
		}
	},

	fetch_retention_release_details(frm, reset_amount) {
		if (!frm.doc.reference_doctype || !frm.doc.reference_name) {
			return;
		}

		frm.retention_release_fetching = true;
		frappe.call({
			method:
				"erpnext.accounts.doctype.retention_release_entry.retention_release_entry.get_retention_release_details",
			args: {
				reference_doctype: frm.doc.reference_doctype,
				reference_name: frm.doc.reference_name,
				retention_amount: reset_amount ? null : frm.doc.retention_amount,
			},
			callback(r) {
				if (!r.message) {
					return;
				}

				frm.set_value(r.message);
			},
			always() {
				frm.retention_release_fetching = false;
			},
		});
	},
});
