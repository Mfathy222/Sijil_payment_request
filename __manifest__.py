{
    'name': 'Sijil_payment_request',
    'version': '19.0.2.0.2',
    'summary': 'Create product-based payment requests from Sales quotations and orders',
    'category': 'Sales/Sales',
    'author': 'SIJILACC',
    'contributors': [
        'Mohamed Fathy. <mohammed.fathy222@gmail.com>',
    ],
    'license': 'LGPL-3',
    'depends': ['sale_management', 'mail'],
    'data': [
        'security/payment_request_security.xml',
        'security/ir.model.access.csv',
        'data/payment_request_sequence.xml',
        'report/payment_request_report.xml',
        'report/payment_request_report_sijil.xml',
        'wizard/payment_request_wizard_views.xml',
        'wizard/sale_advance_payment_inv_views.xml',
        'views/payment_request_views.xml',
        'views/sale_order_views.xml',
        'views/payment_request_menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'Sijil_payment_request/static/src/css/payment_request_ltr.css',
        ],
    },
    'installable': True,
    'application': False,
}
