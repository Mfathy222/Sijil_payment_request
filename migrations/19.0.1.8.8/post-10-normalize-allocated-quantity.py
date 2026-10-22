from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    lines = env['sale.payment.request.line'].search([])

    for line in lines:
        percentage = line.payment_percentage or 0.0
        if percentage <= 0.0 or percentage == 100.0:
            continue

        # Old semantics stored the selected/base Quantity and applied Payment %
        # to the amount. New semantics store the allocated quantity itself.
        line.with_context(skip_payment_request_quantity_migration=True).write({
            'product_uom_qty': line.product_uom_qty * percentage / 100.0,
        })
