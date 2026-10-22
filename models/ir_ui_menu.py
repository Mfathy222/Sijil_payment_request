from odoo import models
from odoo.http import request

from .constants import PAYMENT_REQUEST_COMPANY_IDS


class IrUiMenu(models.Model):
    _inherit = 'ir.ui.menu'

    def _sijil_active_company_id(self):
        """Return the active company selected in Odoo's company switcher.

        Odoo stores the selected companies in the ``cids`` cookie, with the
        active/current company as the first id.  Fall back to env.company when
        no HTTP request/cookie exists (cron/tests/shell).
        """
        if request:
            cookie_value = request.httprequest.cookies.get('cids', '')
            if cookie_value:
                first_company = cookie_value.split('-')[0]
                if first_company.isdigit():
                    return int(first_company)
        return self.env.company.id

    def load_web_menus(self, debug):
        menus = super().load_web_menus(debug)

        if self._sijil_active_company_id() in PAYMENT_REQUEST_COMPANY_IDS:
            return menus

        menu = self.env.ref(
            'Sijil_payment_request.menu_Sijil_payment_request',
            raise_if_not_found=False,
        )
        if not menu:
            return menus

        menu_id = menu.id

        # Remove this menu from every parent's children list.
        for menu_data in menus.values():
            if not isinstance(menu_data, dict):
                continue
            children = menu_data.get('children')
            if isinstance(children, list) and menu_id in children:
                menu_data['children'] = [
                    child_id for child_id in children if child_id != menu_id
                ]

        # Remove the menu itself from the webclient payload.
        menus.pop(menu_id, None)
        return menus
