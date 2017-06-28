# -*- coding: utf-8 -*-
from odoo import tools, api, fields, models, _
import time


class report_launcher(models.TransientModel):
    _name = 'report.launcher'

    report = fields.Selection([
        ('buying_forecast', 'Buying Forecast'),
        ('buying_forecast2', 'Buying Forecast 2'),
        ('distressed_stock', 'Distressed Stock'),
        ('product_sales_data', 'Product Sales Data (For Triple Exp.)'),
    ])

    @api.model
    def default_get(self, fields):
        res = super(report_launcher, self).default_get(fields)
        res['report'] = self._context.get('report')
        return res

    @api.multi
    def update_and_open_report(self):
        # self.env['product.product'].calcuation_of_forecasts([])
        # self.env['product.product'].update_sales_data_and_forecast_cron()

        sobj = self[0]
        if sobj.report == 'buying_forecast':
            name = 'Buying Forecast'
        else:
            name = 'Distressed Stock'
        return {
            'name': name,
            'view_type': 'form',
            'view_mode': 'form',
            'res_id': self._ids[0],
            'res_model': 'report.launcher',
            'type': 'ir.actions.act_window',
            'target': 'new',
        }

    # Triple Exp.

    @api.multi
    def update_and_open_buying_forecast2_report(self):
        print "Update and open Buying forecase2 report"
        start_time = time.time()
        prod_pool = self.env['product.product']

        prod_ids = prod_pool.search([('type', '=', 'product')])

        prod_ids_chunks = [prod_ids[x:x + 50] for x in xrange(0, len(prod_ids), 50)]
        count = len(prod_ids_chunks)
        for chunk in prod_ids_chunks:
            count -= 1
            print count, " To go"
            prod_pool.update_sales_data(chunk)
            prod_pool.update_triple_exp_forecast(chunk)

        print("--- %s seconds ---" % (time.time() - start_time))
