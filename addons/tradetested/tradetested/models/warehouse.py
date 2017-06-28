# -*- encoding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError, except_orm, ValidationError
from odoo.exceptions import UserError, except_orm, ValidationError
from odoo.addons import decimal_precision as dp

import logging
import math

_logger = logging.getLogger(__name__)

class StockWarehouse(models.Model):
    _inherit = 'stock.warehouse'

    default_tax_id = fields.Many2one('account.tax', 'Tax')
    active = fields.Boolean('Active', default=True)


class AmfPostcode(models.Model):
    _name = 'amf.postcode'

    name = fields.Char('Postcode', size=64, required=True)

    _sql_constraints = [
        ('name_uniq', 'unique (name)', 'The Postcode must be unique !'),
    ]


class stock_warehouse_orderpoint(models.Model):
    _inherit = 'stock.warehouse.orderpoint'

    qty_automatic = fields.Boolean('Automatic', default=False)
    state = fields.Selection(related='product_id.state', selection=[('', ''), ('draft', 'In Development'), ('sellable', 'Normal'), ('end', 'End of Lifecycle'), ('obsolete', 'Obsolete')], string='Status')
    product_min_qty = fields.Float('Minimum Quantity', required=True, digits=dp.get_precision('Product Unit of Measure'), default=0)
    product_max_qty = fields.Float('Maximum Quantity', required=True, digits=dp.get_precision('Product Unit of Measure'), default=0)

    @api.onchange('product_id')
    def onchange_product_id(self):
        if self.product_id:
            prod = self.env['product.product']
            d = {'product_uom': [('category_id', '=', prod.uom_id.category_id.id)]}
            v = {'product_uom': prod.uom_id.id, 'company_id': prod.company_id.id}
            return {'value': v, 'domain': d}
        return {'domain': {'product_uom': []}}

    @api.multi
    def update_min_max_qty(self):
        for order_point in self:
            if order_point.product_id.sales_data:
                print order_point.product_id.sales_data[12].sequence
                period_1 = [sd for sd in order_point.product_id.sales_data if sd.sequence == -1]
                period_2 = [sd for sd in order_point.product_id.sales_data if sd.sequence == -2]
                if period_1 and period_2:
                    min_qty = math.ceil(period_1[0].avg_for_estimate + 1)
                    max_qty = math.ceil(period_1[0].avg_for_estimate + period_2[0].avg_for_estimate + 2)
                    order_point.write({'product_min_qty': min_qty, 'product_max_qty': max_qty})
        return True

    @api.model
    def update_min_max_qty_cron(self):
        order_points = self.search([('qty_automatic', '=', True)])
        _logger.info('Updating Reordering Rules: %s' % len(order_points))
        order_points.update_min_max_qty()


class StockLocation(models.Model):
    _inherit = 'stock.location'

    complete_name = fields.Char(compute='_complete_name', string="Full Location Name", store=True)

    @api.multi
    @api.depends('location_id', 'name', 'location_id.name')
    def _complete_name(self):
        for loc in self:
            name = loc.name
            parent = loc.location_id
            while parent:
                name = parent.name + ' / ' + name
                parent = parent.location_id
            loc.complete_name = name


class DeliveryCarrier(models.Model):
    _inherit = 'delivery.carrier'

    tracking_url = fields.Char('Tracking URL', help="Use {TRACKING_REF} variable to specify tracking number placeholder")
    company_id = fields.Many2one('res.company', 'Company')
