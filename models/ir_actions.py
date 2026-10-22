from odoo import api, models

from .constants import (
    SIJIL_PAYMENT_REQUEST_COMPANY_ID,
    ZOFIRM_PAYMENT_REQUEST_COMPANY_ID,
)


class IrActions(models.Model):
    _inherit = 'ir.actions.actions'

    @api.model
    def get_bindings(self, model_name):
        bindings = super().get_bindings(model_name)

        if model_name != 'sale.payment.request':
            return bindings

        result = {
            binding_type: list(actions)
            for binding_type, actions in bindings.items()
        }

        reports = result.get('report', [])

        snb_zofirm = self.env.ref(
            'Sijil_payment_request.action_report_payment_request_snb_list',
            raise_if_not_found=False,
        )
        alinma_zofirm = self.env.ref(
            'Sijil_payment_request.action_report_payment_request_alinma_list',
            raise_if_not_found=False,
        )
        alinma_sijil = self.env.ref(
            'Sijil_payment_request.action_report_payment_request_alinma_sijil_list',
            raise_if_not_found=False,
        )

        custom_ids = {
            action.id
            for action in (snb_zofirm, alinma_zofirm, alinma_sijil)
            if action
        }

        unrelated = [
            action for action in reports
            if action.get('id') not in custom_ids
        ]

        if self.env.company.id == ZOFIRM_PAYMENT_REQUEST_COMPANY_ID:
            allowed_ids = {
                action.id
                for action in (snb_zofirm, alinma_zofirm)
                if action
            }
        elif self.env.company.id == SIJIL_PAYMENT_REQUEST_COMPANY_ID:
            allowed_ids = {alinma_sijil.id} if alinma_sijil else set()
        else:
            allowed_ids = set()

        own_allowed = [
            action for action in reports
            if action.get('id') in allowed_ids
        ]

        result['report'] = unrelated + own_allowed
        return result
