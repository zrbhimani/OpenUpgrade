# -*- coding: utf-8 -*-
from odoo import tools, api, fields, models, _


class mrp_bom(models.Model):
    _inherit = 'mrp.bom'

    product_id = fields.Many2one('product.product', 'Product', domain="[('type', 'in', ['product', 'consu'])]")
    possible_stock_on_hand = fields.Float(compute='_get_possible_stock', string="Possible Quantity on Hand")
    fm_header_sku = fields.Char('Orig. FM Header')
    bom_volume = fields.Float(compute='_cal_bom_values', string="Volume", store=True)
    bom_volume_container = fields.Float(compute='_cal_bom_values', string="Container Volume", store=True)
    bom_volume_storage = fields.Float(compute='_cal_bom_values', string="Storage Volume", store=True)
    bom_weight = fields.Float(compute='_cal_bom_values', string="Gross Weight", store=True)
    bom_weight_net = fields.Float(compute='_cal_bom_values', string="Net Weight", store=True)
    bom_standard_price = fields.Float(compute='_cal_bom_values', string="Cost Price", store=True)

    @api.multi
    def _get_possible_stock(self):
        for bom in self:
            if bom.bom_line_ids:
                bom.possible_stock_on_hand = min([line.product_id.qty_available / line.product_qty for line in bom.bom_line_ids])

    @api.multi
    @api.depends('bom_line_ids', 'bom_line_ids.product_id', 'bom_line_ids.product_qty', 'bom_line_ids.product_id.volume', 'bom_line_ids.product_id.volume_container',
                 'bom_line_ids.product_id.volume_storage', 'bom_line_ids.product_id.weight', 'bom_line_ids.product_id.weight_net', 'bom_line_ids.product_id.standard_price')
    def _cal_bom_values(self):
        for bom in self:
            bom_vals = {'bom_volume': 0.0, 'bom_volume_container': 0.0, 'bom_volume_storage': 0.0, 'bom_weight': 0.0, 'bom_weight_net': 0.0, 'bom_standard_price': 0.0}
            for line in bom.bom_line_ids:
                bom_vals['bom_volume'] += line.product_id.volume * line.product_qty
                bom_vals['bom_volume_container'] += line.product_id.volume_container * line.product_qty
                bom_vals['bom_volume_storage'] += line.product_id.volume_storage * line.product_qty
                bom_vals['bom_weight'] += line.product_id.weight * line.product_qty
                bom_vals['bom_weight_net'] += line.product_id.weight_net * line.product_qty
                bom_vals['bom_standard_price'] += line.product_id.standard_price * line.product_qty
            bom.update(bom_vals)

    @api.onchange('product_id')
    def onchange_product_id(self):
        if self.product_id:
            self.product_tmpl_id = self.product_id.product_tmpl_id.id
            self.product_uom_id = self.product_id.uom_id.id
            boms = self.env['mrp.bom'].search([('product_id', '=', self.product_id.id)], order='sequence desc', limit=1)
            if boms:
                self.sequence = boms[0].sequence + 1

    @api.multi
    def get_all_affected_product_ids(self):
        sql = """
            WITH descendant_product as (
                SELECT l.product_id, b.id as bom_id from mrp_bom_line l, mrp_bom b WHERE l.bom_id = b.id and b.id = %s
            )
            SELECT product_id, bom_id from descendant_product 
            UNION
            SELECT b.product_id, dp.bom_id from mrp_bom b, mrp_bom_line l, descendant_product dp WHERE l.bom_id = b.id and l.product_id = dp.product_id        
        """ % self.id
        self._cr.execute(sql)
        return [r[0] for r in self._cr.fetchall()]
