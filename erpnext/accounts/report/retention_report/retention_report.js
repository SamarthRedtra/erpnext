frappe.query_reports["Retention Report"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			reqd: 1,
		},
		{
			fieldname: "invoice_type",
			label: __("Invoice Type"),
			fieldtype: "Select",
			options: "\nSales Invoice\nPurchase Invoice",
			on_change: function () {
				const invoice_type = frappe.query_report.get_filter_value("invoice_type");
				frappe.query_report.set_filter_value(
					"party_type",
					invoice_type === "Purchase Invoice" ? "Supplier" : "Customer"
				);
				frappe.query_report.set_filter_value("party", "");
			},
		},
		{
			fieldname: "project",
			label: __("Project"),
			fieldtype: "Link",
			options: "Project",
		},
		{
			fieldname: "party",
			label: __("Party"),
			fieldtype: "Dynamic Link",
			options: "party_type",
		},
		{
			fieldname: "party_type",
			label: __("Party Type"),
			fieldtype: "Select",
			options: "\nCustomer\nSupplier",
			hidden: 1,
			default: "Customer",
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
		},
		{
			fieldname: "release_date",
			label: __("Release Due By"),
			fieldtype: "Date",
		},
	],
	formatter(value, row, column, data, default_formatter) {
		if (column.fieldname === "release_retention" && data?.release_retention) {
			const label = __("Release Retention");
			return `<button class="btn btn-sm bg-primary text-white release-retention"
				data-reference-doctype="${frappe.utils.escape_html(data.invoice_type)}"
				data-reference-name="${frappe.utils.escape_html(data.invoice)}">${label}</button>`;
		}

		return default_formatter(value, row, column, data);
	},
	onload(report) {
		$(document).on("click", ".release-retention", function () {
			frappe.new_doc("Retention Release Entry", {
				reference_doctype: $(this).attr("data-reference-doctype"),
				reference_name: $(this).attr("data-reference-name"),
			});
		});

		report.page.add_inner_button(__("Release Retention"), () => {
			const filters = report.get_values();
			const dialog = new frappe.ui.Dialog({
				title: __("Release Retention"),
				fields: [
					{
						fieldname: "reference_doctype",
						label: __("Reference Document Type"),
						fieldtype: "Select",
						options: "Sales Invoice\nPurchase Invoice",
						default: filters.invoice_type || "Sales Invoice",
						reqd: 1,
					},
					{
						fieldname: "reference_name",
						label: __("Reference Document"),
						fieldtype: "Dynamic Link",
						options: "reference_doctype",
						reqd: 1,
						get_query() {
							const query_filters = {
								company: filters.company,
								docstatus: 1,
								enable_retention: 1,
							};
							if (filters.project) {
								query_filters.project = filters.project;
							}
							return {
								filters: query_filters,
							};
						},
					},
				],
				primary_action_label: __("Create Release Entry"),
				primary_action(values) {
					dialog.hide();
					frappe.new_doc("Retention Release Entry", {
						reference_doctype: values.reference_doctype,
						reference_name: values.reference_name,
					});
				},
			});
			dialog.show();
		});
	},
};
