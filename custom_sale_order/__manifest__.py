# -*- coding: utf-8 -*-
{
    'name': "Sale Customisation",
    'version': '17.0.1.0.0',
    'category': 'Sale',
    'summary': "Customizations of Sale Module",
    'description': """
                    This module extends the Sales module, with:
                    - A custom user group 'Sale Admin' with permissions.
                    - Implements sale order limit checks.
                    - Automates the sales workflow from order to payment.
                    - Adds validation and security for sales order confirmation.""",
    'author': "Test",
    'company': "Zinfog",
    'website':"www.zincoftest.com",
    'depends': ['base', 'sale', 'account',
                ],
    'data': [
        'security/groups.xml',
        'security/ir.model.access.csv',
        'views/sale_order_views.xml',

    ],
    'license': "AGPL-3",
    'installable': True,
    'auto_install': False,
    'application': False,
}
