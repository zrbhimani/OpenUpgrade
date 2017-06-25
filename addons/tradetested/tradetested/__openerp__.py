# -*- coding: utf-8 -*-
{
    'name': 'Tradetested',
    'version': '1.1',
    'category': 'Tradetested',
    'sequence': 1,
    'summary': '',
    'description': """

            TradeTested Customisation

    """,
    'author': 'Zahir',
    'website': '',
    'depends': ['sale_stock', 'purchase', 'account', 'mail', 'product', 'board', 'mrp', 'crm', 'l10n_nz', 'stock_dropshipping', 'purchase_mrp', 'delivery'],
    'data': [

        'security/security.xml',
        'security/ir.model.access.csv',

        'views/tradetested.xml',

        'wizard/case_options.xml',
        'wizard/open_message.xml',
        'wizard/product_options.xml',
        'wizard/po_options.xml',
        'wizard/so_options.xml',
        'wizard/stock_options.xml',

        'views/email_templates.xml',
        'views/case.xml',
        'views/product.xml',
        'views/partner.xml',
        'views/warehouse.xml',
        'views/stock.xml',
        'views/bom.xml',
        'views/purchase.xml',
        'views/sale.xml',
        'views/conn_competitor.xml',
        'views/conn_geckoboard.xml',
        'views/conn_rabbitmq.xml',
        'views/conn_mailchimp.xml',
        'views/conn_scs_export.xml',
        'views/conn_scs_import.xml',
        'views/conn_zendesk.xml',
        'views/conn_sql_export.xml',
        'views/conn_payment_expr.xml',
        'views/conn_delivery_track.xml',
        'views/conn_xero.xml',
        'views/sale_return.xml',
        'views/obj_watchers.xml',
        'views/data.xml',
        'views/crm.xml',
        'views/base.xml',
        'views/menus.xml',

        'report/case_report.xml',
        'report/sale_report.xml',
        'report/product_report.xml',
        'report/purchase_report.xml',
        'report/stock_report.xml',

        'print/header.xml',
        'print/layouts.xml',
        'print/report_saleorder.xml',
        'print/report_purchasequotation.xml',
        'print/report_deliveryslip.xml',
        'print/report_print_case.xml',

        'wizard/report_launcher.xml',

        'report.xml',

    ],
    'demo': [],
    'css': [],
    'js': [],
    'qweb': [
        'static/src/xml/tradetested.xml',
        'static/src/xml/watcher.xml',
    ],
    'installable': True,
    'auto_install': False,
    'application': True,
    'external_dependencies': {
        'python' : ['html2text','kombu'],
    },
}
