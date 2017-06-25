# -*- coding: utf-8 -*-
from odoo import tools, api, fields, models, _


class update_price_margin(models.TransientModel):
    _name = 'update.price.margin'

    margin = fields.Float('Margin')
    price = fields.Float('Price')
    state = fields.Char('State')

    @api.model
    def default_get(self, fields):
        resp = super(update_price_margin, self).default_get(fields)
        resp['state'] = self._context.get('type', '')
        return resp

    @api.multi
    def do_update(self):
        sobj = self[0]
        product = self.env['product.product'].browse([self._context['active_id']])
        if sobj.state == 'update_price':
            product.write({'price_for_margin': sobj.price})
        elif sobj.state == 'update_margin':
            product.write({'margin_for_price': sobj.margin})
        return True


class bulk_update_leadtime(models.TransientModel):
    _name = 'bulk.update.leadtime'

    delay = fields.Integer('Delivery Lead Time')

    @api.multi
    def do_update_delay(self):

        if self._context.get('active_model') == 'res.partner':
            self.env['product.supplierinfo'].search([('name', '=', self._context['active_id'])]).write({'delay': self.delay})

        elif self._context.get('active_model') == 'product.product':
            for product in self.env['product.product'].browse(self._context['active_ids']):
                for seller in product.seller_ids:
                    seller.write({'delay': self.delay})

        return True
