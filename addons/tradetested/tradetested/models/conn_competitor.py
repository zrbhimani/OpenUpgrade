# -*- coding: utf-8 -*-
import requests
import time
from datetime import datetime
from lxml import html, etree
from odoo.tools.safe_eval import safe_eval
from odoo import tools, api, fields, models, _
from odoo.exceptions import UserError

import logging

_logger = logging.getLogger('Price Monitor')


class price_monitor(models.Model):
    _name = 'price.monitor'

    name = fields.Char('Name')
    url = fields.Char('URL')
    code = fields.Text('Python Code', default='price = 0')
    line_ids = fields.One2many('price.monitor.line', 'monitor_id', 'Lines')
    price_ids = fields.One2many('price.monitor.price', 'monitor_id', 'Prices')
    test_code = fields.Text('Test Code', default='product_page=True')

    @api.multi
    def get_price(self):
        current_time = time.strftime('%Y-%m-%d %H:%M:00')

        if self._context.get('line_id'):
            lines = [self.env['price.monitor.line'].browse(self._context['line_id'])]
            sobj = lines[0].monitor_id
            error_handling = 'raise'
        else:
            sobj = self[0]
            lines = sobj.line_ids
            error_handling = 'log'

        parse_code = sobj.code
        header = {
            'user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/41.0.2272.118 Safari/537.36',
        }

        if sobj.url:
            host = sobj.url.replace('http://', '').replace('https://', '').split("/")[0]
            header['host'] = host

        session = requests.session()
        for line in lines:
            try:
                web_url = line.external_sku
                page = requests.get(web_url.strip(), headers=header, timeout=15, allow_redirects=True, verify=False)
                if page.status_code != 200:
                    line.write({'status': 'error', 'err_msg': page.status_code})
                    continue

                ctx = {
                    'self': self,
                    'cr': self._cr,
                    'uid': self.env.uid,
                    'time': time,
                    'datetime': datetime,
                    'user': self.env.user,
                    'page': page,
                    'tree': html.fromstring(page.content),
                    '_logger': _logger,
                }
                safe_eval(sobj.test_code.strip(), ctx, mode="exec", nocopy=True)
                if ctx.get('product_page') == True:

                    safe_eval(parse_code.strip(), ctx, mode="exec", nocopy=True)

                    if ctx.get('price') and float(ctx['price']) > 0:
                        status = ctx.get('status', '')
                        prices = self.env['price.monitor.price'].search([('line_id', '=', line.id)])
                        updated = False
                        if prices:
                            price = prices[0]
                            if (abs(price.price - float(ctx['price'])) <= 0.01) and (status == price.status):
                                price.update({'date': current_time, 'current': True})
                                updated = True

                        if not updated:
                            self._cr.execute("UPDATE price_monitor_price SET current=False WHERE product_id=%s AND monitor_id=%s and current=True" % (line.product_id.id, line.monitor_id.id))
                            self.env['price.monitor.price'].create({
                                'monitor_id': line.monitor_id.id,
                                'line_id': line.id,
                                'date': current_time,
                                'first_date': current_time,
                                'price': float(ctx['price']),
                                'status': status,
                                'current': True,
                            })
                        line.update({'last_update': current_time, 'last_price': float(ctx['price']), 'status': 'ok', 'err_msg': ''})
                else:
                    line.write({'status': 'error', 'err_msg': 'Not product page'})
            except Exception, e:
                line.update({'status': 'error', 'err_msg': 'Failed parsing'})
                if error_handling == 'raise':
                    raise UserError('Error in Fetching Price\n %s\n\n SKU: %s\n\n External URL: %s\n\n Error: %s' % (sobj.name, line.product_id.code, line.external_sku, str(e)))
                else:
                    _logger.error('%s, SKU: %s, External URL: %s, Error: %s' % (sobj.name, line.product_id.code, line.external_sku, str(e)))
                continue
        return True

    @api.model
    def get_price_cron(self, ids=None):
        _logger.info('Cron Starting')
        monitors = self.search([])
        for monitor in monitors:
            monitor.get_price()
        _logger.info('Cron Finished')
        return True


class price_monitor_line(models.Model):
    _name = 'price.monitor.line'
    _log_access = False

    monitor_id = fields.Many2one('price.monitor', 'Monitor', ondelete='restrict')
    last_update = fields.Datetime('Updated')
    last_price = fields.Float('Price')
    status = fields.Char('Status')
    product_id = fields.Many2one('product.product', string="Product")
    external_sku = fields.Char('External URL', size=1024)
    err_msg = fields.Char('Last Error')

    _sql_constraints = [
        ('config_uniq', 'unique(monitor_id, product_id)', 'Product already configured for this monitor, only one external sku is allowed'),
    ]

    @api.multi
    def open_website(self):
        if self[0].external_sku.startswith('http'):
            web_url = self[0].external_sku
        else:
            web_url = self[0].monitor_id.url.replace('{EXTERNAL_SKU}', self[0].external_sku)
        return {
            'name': 'Go to website',
            'type': 'ir.actions.act_url',
            'target': 'new',
            'url': web_url,
        }

    @api.model
    def create(self, vals):
        line = super(price_monitor_line, self).create(vals)
        line.monitor_id.with_context({'line_id': line.id}).get_price()
        return line

    @api.multi
    def write(self, vals):
        resp = super(price_monitor_line, self).write(vals)
        if 'external_sku' in vals:
            for rec in self:
                rec.monitor_id.with_context({'line_id': rec.id}).get_price()
        return resp

    @api.multi
    def get_price(self):
        return self.monitor_id.with_context({'line_id': self.id}).get_price()

class price_monitor_price(models.Model):
    _name = 'price.monitor.price'
    _order = 'date desc, id desc'

    monitor_id = fields.Many2one('price.monitor', 'Monitor')
    line_id = fields.Many2one('price.monitor.line', 'Line', ondelete='cascade')
    product_id = fields.Many2one('product.product', related='line_id.product_id', string="Product", store=True)
    date = fields.Datetime('Last Seen at')
    first_date = fields.Datetime('First Seen at')
    price = fields.Float('Price')
    status = fields.Char('Stock Status', size=256)
    current = fields.Boolean('Current')

    @api.multi
    def open_website(self):
        if self[0].line_id.external_sku.startswith('http'):
            web_url = self[0].line_id.external_sku
        else:
            web_url = self[0].monitor_id.url.replace('{EXTERNAL_SKU}', self[0].line_id.external_sku)
        return {
            'name': 'Go to website',
            'type': 'ir.actions.act_url',
            'target': 'new',
            'url': web_url,
        }

class product_comp_price(models.Model):
    _name = 'product.comp.price'
    _auto = False
    _order = 'diff_price desc'
    _rec_name = 'product_id'

    product_id = fields.Many2one('product.product', 'Product')
    categ_id = fields.Many2one('product.category', 'Category')
    line_id = fields.Many2one('price.monitor.line', 'Line')
    sale_price = fields.Float('Our Best Price')
    comp_price = fields.Float('Lowest Competitor Price')
    monitor_id = fields.Many2one('price.monitor', 'Competitor')
    diff_price = fields.Float('Difference (%)')
    note = fields.Char(compute='_note', string="Notes")

    @api.multi
    def _note(self):
        for comp in self:
            comp.note = comp.product_id.pmp_note

    @api.model_cr
    def init(self):
        tools.drop_view_if_exists(self._cr, 'product_comp_price')
        self._cr.execute("""
                create or replace view product_comp_price as (
                    WITH product_data as (
                        select
                            p.id as product_id,
                            t.id as template_id,
                            t.categ_id as categ_id,
                            CASE WHEN p.special_price>0 THEN p.special_price ELSE t.list_price END as sale_price
                        FROM
                            product_product p, product_template t
                        WHERE
                            p.product_tmpl_id = t.id AND
                            p.id in ( SELECT distinct product_id from price_monitor_price )
                    )
                    SELECT
                        max(pmp.id) as id,
                        pmp.product_id as product_id,
                        pd.categ_id as categ_id,
                        min(pmp.price) as comp_price,
                        pd.sale_price as sale_price,
                        (select monitor_id from price_monitor_price WHERE product_id = pmp.product_id and price = min(pmp.price) order by monitor_id limit 1) as monitor_id,
                        (select line_id from price_monitor_price WHERE product_id = pmp.product_id and price = min(pmp.price) order by monitor_id limit 1) as line_id,
                        (( pd.sale_price - min(pmp.price) ) / min(pmp.price) ) * 100 as diff_price
                    FROM
                        price_monitor_price pmp, product_data pd
                    WHERE
                        pd.product_id = pmp.product_id AND pmp.current = True

                    GROUP BY
                        pmp.product_id,
                        pd.categ_id,
                        pd.sale_price
                )""")

    @api.multi
    def open_product(self):
        return {
            'type': 'ir.actions.act_window',
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'product.product',
            'res_id': self[0].product_id.id,
            'target': 'current',
        }

    @api.multi
    def open_website(self):
        if self[0].line_id:
            return self[0].line_id.open_website()

class product_comp_url(models.Model):
    _name = 'product.comp.url'
    _auto = False
    _order = 'nbr_error desc, nbr_comp'
    _rec_name = 'product_id'

    product_id = fields.Many2one('product.product', 'Product', readonly=True)
    categ_id = fields.Many2one('product.category','Category of Product', readonly=True)
    default_code = fields.Char('SKU')
    sale_ok = fields.Boolean('Can be Sold')
    purchase_ok = fields.Boolean('Can be Purchased')
    active = fields.Boolean('Active')
    company_id = fields.Many2one('res.company', 'Company', readonly=True)
    type = fields.Selection([('consu', 'Consumable'),('service','Service'),('product','Product')], string='Product Type')
    supplier_id = fields.Many2one('res.partner', 'Supplier')
    nbr_comp = fields.Integer('# Competitors')
    nbr_error = fields.Integer('# Errors')
    monitor_id = fields.Many2one('price.monitor', 'Competitor')

    @api.model_cr
    def init(self):
        tools.drop_view_if_exists(self._cr, 'product_comp_url')
        self._cr.execute("""
                create or replace view product_comp_url as (
                    WITH pml_errors as(
                        select * from price_monitor_line WHERE status='error'
                    )
                    SELECT
                        row_number() over () as id,
                        p.id as product_id,
                        t.categ_id as categ_id,
                        t.type as type,
                        p.default_code as default_code,
                        t.company_id as company_id,
                        p.active as active,
                        t.purchase_ok as purchase_ok,
                        t.sale_ok as sale_ok,
                        s.name as supplier_id,
                        count(pml.id) as nbr_comp,
                        count(pmle.id) as nbr_error,
                        pml.monitor_id as monitor_id
                    from
                        product_product p
                        LEFT JOIN product_template t on p.product_tmpl_id = t.id
                        LEFT JOIN product_supplierinfo s on s.product_id = t.id
                        LEFT JOIN price_monitor_line pml on pml.product_id = p.id
                        LEFT JOIN pml_errors pmle on pmle.product_id = p.id
                    group by
                        p.id, t.categ_id, s.name, t.type, t.company_id, t.purchase_ok, t.sale_ok, pml.monitor_id
                    order by
                        nbr_error desc, nbr_comp
                )""")

    @api.model
    def read_group(self, domain, fields, groupby, offset=0, limit=None, orderby=False, lazy=True):
        resp = super(product_comp_url, self).read_group(domain, fields, groupby, offset, limit, orderby, lazy)
        resp = sorted(resp, key=lambda x: (-x['nbr_error'], x['nbr_comp']))
        return resp

class pm_update_note(models.TransientModel):
    _name = 'pm.update.note'

    note = fields.Char('Note', size=1024)

    @api.one
    def add_note(self):
        obj = self.env['product.comp.price'].browse(self._context['active_id'])
        if obj.product_id:
            obj.product_id.write({'pmp_note': self.note})
        return True


