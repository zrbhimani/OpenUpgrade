# -*- coding: utf-8 -*-
from odoo import tools, api, fields, models, _
from lxml import html, etree

import json
import requests
import time
import logging
import traceback
import sys

import logging
_logger = logging.getLogger('Shipment Tracking')

AVAILABLE_PARSER = [
    ('courierpost_nz', 'Courierpost NZ'),
    ('pbt_nz', 'PBT NZ'),
    ('toll_nz', 'Toll NZ'),

    ('toll_au', 'Toll AU'),
    ('startrack_au', 'Startrack AU')

]

# logging.getLogger("urllib3").setLevel(logging.WARNING)
# logging.getLogger("requests").setLevel(logging.WARNING)

class shipment_tracking_log(models.Model):
    _name = 'shipment.tracking.log'
    _order = 'timestamp desc'
    _log_access = False

    tracker_id = fields.Many2one('shipment.tracking', 'Shipment Tracker')
    message = fields.Char('Message')
    timestamp = fields.Datetime('Timestamp', default=lambda *a: time.strftime('%Y-%m-%d %H:%M:%S'))


class shipment_tracking(models.Model):
    _name = 'shipment.tracking'

    name = fields.Char('Name')
    url = fields.Char('URL', size=1024, help="(Use Variable {TRACKING_REF})")
    function = fields.Selection(AVAILABLE_PARSER, string="Parser Function")
    log_ids = fields.One2many('shipment.tracking.log', 'tracker_id', 'Logs')
    picking_counts = fields.Integer(compute='_picking_counts', string="# of DO. Matching Search Criteria")
    search_args = fields.Text('Search Criteria', default = """[
                                        ('state','=','done'),
                                        ('date_done', '>=', '2015-01-20 00:00:00'),
                                        ('tracking_status','in',['New','In Transit',False]),
                                        ('carrier_tracking_ref','!=',''),
                                        ('carrier_id.name','=','CARRIER_NAME'),
                                        ('company_id.name','=','Australia'),
                                        '|',
                                        ('tracking_last_up','=',False),
                                        ('tracking_last_up','<', time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time() - (2 * 60 * 60)))),
                                    ]"""
                                )

    @api.multi
    def _picking_counts(self):
        for track in self:
            track.picking_counts = self.env['stock.picking'].search_count(eval(track.search_args))

    @api.multi
    def picking_show(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Delivery Order',
            'res_model': 'stock.picking',
            'view_type': 'form',
            'view_mode': 'tree,form',
            'target': 'current',
            'nodestroy': True,
            'domain': eval(self[0].search_args)
        }

    def run(self):
        if not self._ids:
            ids = self.search([])

        tracker_list = [(t.picking_counts, t) for t in self]
        tracker_list.sort()

        for count, tracker in tracker_list:
            _logger.info("%s: %s" % (tracker.name, count))
            try:
                eval("tracker." + tracker.function + "()")
            except Exception, e:
                traceback.print_exc(file=sys.stdout)
                log_pool = self.env['shipment.tracking.log']
                log_pool.create({'tracker_id': tracker.id, 'message': str(e)})
        return True

    @api.multi
    def clear_logs(self):
        sobj = self[0]
        return self.env['shipment.tracking.log'].unlink([l.id for l in sobj.log_ids])

    @api.multi
    def toll_au(self):
        tracking_status_map = {
            'IN_TRANSIT': 'In Transit',
            'OFD': 'In Transit',
            'DELIVERED': 'Complete'
        }

        sobj = self[0]
        if sobj.function != 'toll_au':
            return

        headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
        picking_pool = self.env['stock.picking']
        log_pool = self.env['shipment.tracking.log']
        search_args = eval(sobj.search_args)
        pickings = picking_pool.search(search_args, order='date_done')

        log_pool.create({'tracker_id': sobj.id, 'message': "Started, %s to check" % len(pickings)})

        session = requests.session()
        for sub_pickings in [pickings[i:i + 10] for i in range(0, len(pickings), 10)]:
            connotes = [picking.carrier_tracking_ref for picking in sub_pickings]
            payload = {"connoteIds": ",".join(connotes), "systemId": None}
            r = requests.post(sobj.url, data=json.dumps(payload), headers=headers)
            data = json.loads(r.text)

            for resp in data['tatConnotes']:
                tracking_status = resp['progressStatusCode']
                if tracking_status:
                    picking_found_ids = picking_pool.search([('carrier_tracking_ref', '=', resp['connote']), ('state', '=', 'done')], limit=1)
                    if picking_found_ids:
                        vals = {
                            'tracking_status': tracking_status in tracking_status_map and tracking_status_map[tracking_status] or 'In Transit',
                            'tracking_notes': tracking_status + "\n" + resp['lastEventDateTime'],
                            'tracking_last_up': time.strftime('%Y-%m-%d %H:%M:00')
                        }
                        picking_pool.write(picking_found_ids, vals)

        log_pool.create({'tracker_id': sobj.id, 'message': "Finishing"})

    @api.multi
    def courierpost_nz(self):
        tracking_status_map = {
            'Delivered': 'Complete',
            'Awaiting Collection': 'In Transit',
            'In Transit': 'In Transit',
            'Picked Up': 'In Transit',
            'New': 'New'
        }

        sobj = self[0]
        if sobj.function != 'courierpost_nz':
            return

        picking_pool = self.env['stock.picking']
        log_pool = self.env['shipment.tracking.log']

        search_args = eval(sobj.search_args)
        pickings = picking_pool.search(search_args, order='date_done')

        log_pool.create({'tracker_id': sobj.id, 'message': "Started, %s to check" % len(pickings)})

        session = requests.session()
        for sub_pickings in [pickings[i:i + 10] for i in range(0, len(pickings), 10)]:
            connotes = [picking.carrier_tracking_ref for picking in sub_pickings]
            page = requests.get(sobj.url.replace("{TRACKING_REF}", "_".join(connotes)))

            tree = html.fromstring(page.text)

            shipments = tree.xpath("//div[contains(@id, '_progress')]")
            for shipment in shipments:
                shipment_key = shipment.values()[0]
                shipment_tracking = shipment_key.replace('_progress', '')

                tracking_status = tree.xpath("//div[@id='%s']/div/div/text()" % (shipment_key))
                if tracking_status:
                    tracking_status = tracking_status[0]
                else:
                    tracking_status = 'New'
                tracking_date = tree.xpath("//ul[@data-ticket-number='%s']/li/span[@class='ticket-date']/text()" % (shipment_tracking))[0]

                picking_found_ids = picking_pool.search([('carrier_tracking_ref', '=', shipment_tracking), ('state', '=', 'done')], limit=1)
                if picking_found_ids:
                    vals = {
                        'tracking_status': tracking_status in tracking_status_map and tracking_status_map[tracking_status] or 'In Transit',
                        'tracking_notes': tracking_status + "\n" + tracking_date,
                        'tracking_last_up': time.strftime('%Y-%m-%d %H:%M:00')
                    }
                    picking_pool.write(picking_found_ids, vals)
            self._cr.commit()

        log_pool.create({'tracker_id': sobj.id, 'message': "Finishing"})

    @api.multi
    def pbt_nz(self):
        tracking_status_map = {
            'Delivered': 'Complete',
            'Awaiting Collection': 'In Transit',
            'In Transit': 'In Transit',
            'Picked Up': 'In Transit',
            'New': 'New'
        }

        sobj = self[0]
        if sobj.function != 'pbt_nz':
            return

        picking_pool = self.env['stock.picking']
        log_pool = self.env['shipment.tracking.log']

        search_args = eval(sobj.search_args)
        pickings = picking_pool.search(search_args, order='date_done')

        log_pool.create({'tracker_id': sobj.id, 'message': "Started, %s to check" % len(pickings)})

        session = requests.session()
        for picking in pickings:
            page = requests.get(sobj.url.replace("{TRACKING_REF}", "%s||1|PBT-print" % picking.carrier_tracking_ref))

            data = page.text
            data = data[data.find('.callBack("') + 11:data.find('")')]

            if 'ERROR' in data:
                continue
            elif 'MULTI' in data:
                args_parts = data.split('|')
                page = requests.get(sobj.url.replace("{TRACKING_REF}", "%s|%s|1|PBT-print" % (picking.carrier_tracking_ref, args_parts[2])))

                data = page.text
                data = data[data.find('.callBack("') + 11:data.find('")')]

            tracking_notes = ""
            data_items = data.split('|')
            tracking_notes += data_items[3].split('@VM')[-1]
            tracking_notes += " " + data_items[4].split('@VM')[-1]
            tracking_notes += "\n" + data_items[5].split('@VM')[-1]
            tracking_notes += "\n" + data_items[6].split('@VM')[-1]

            tracking_status = ""
            if 'DELIVERED' in data_items[10]:
                tracking_status = 'Delivered'
            if 'In Transit' in data_items[10]:
                tracking_status = 'In Transit'

            vals = {
                'tracking_status': tracking_status in tracking_status_map and tracking_status_map[tracking_status] or 'In Transit',
                'tracking_notes': tracking_notes,
                'tracking_last_up': time.strftime('%Y-%m-%d %H:%M:00')
            }

            picking.write(vals)

        log_pool.create({'tracker_id': sobj.id, 'message': "Finishing"})

    @api.multi
    def toll_nz(self):
        tracking_status_map = {
            'Delivered': 'Complete',
            'In Transit': 'In Transit',
        }

        sobj = self[0]
        if sobj.function != 'toll_nz':
            return

        picking_pool = self.env['stock.picking']
        log_pool = self.env['shipment.tracking.log']

        search_args = eval(sobj.search_args)
        pickings = picking_pool.search(search_args, order='date_done')

        log_pool.create({'tracker_id': sobj.id, 'message': "Started, %s to check" % len(pickings)})

        session = requests.session()
        for picking in pickings:
            page = requests.get(sobj.url.replace("{TRACKING_REF}", picking.carrier_tracking_ref))

            tree = html.fromstring(page.text)
            last_event = tree.xpath("//div[@class='toll-table']/table/thead/tr/th[text()='Event Description']//ancestor::table/tr[last()]/td/text()")
            tracking_notes = " ".join(last_event)

            tracking_status = ""
            if 'Delivered' in tracking_notes:
                tracking_status = 'Delivered'
            else:
                tracking_status = 'In Transit'

            vals = {
                'tracking_status': tracking_status in tracking_status_map and tracking_status_map[tracking_status] or 'In Transit',
                'tracking_notes': tracking_notes,
                'tracking_last_up': time.strftime('%Y-%m-%d %H:%M:00')
            }
            picking.write(vals)

        log_pool.create({'tracker_id': sobj.id, 'message': "Finishing"})

    @api.multi
    def startrack_au(self):
        tracking_status_map = {
            'Delivered': 'Complete',
            'Delivered in Full': 'Complete',

            'On Board for Delivery': 'In Transit',
            'In Transit': 'In Transit',
            'Partial Delivery': 'In Transit',
            'Unsuccessful Delivery': 'In Transit'
        }

        sobj = self[0]
        if sobj.function != 'startrack_au':
            return

        picking_pool = self.env['stock.picking']
        log_pool = self.env['shipment.tracking.log']

        search_args = eval(sobj.search_args)
        pickings = picking_pool.search(search_args, order='date_done')

        log_pool.create({'tracker_id': sobj.id, 'message': "Started, %s to check" % len(pickings)})

        session = requests.session()
        for picking in pickings:
            page = requests.get(sobj.url.replace("{TRACKING_REF}", picking.carrier_tracking_ref))

            tree = html.fromstring(page.text)
            tracking_status = tree.xpath("//span[@id='__c1_lblStatus']/text()")
            tracking_notes = tree.xpath("//span[@id='__c1_lblScanDateTime']/text()")

            if tracking_status:
                tracking_status = tracking_status[0]

                if tracking_notes:
                    tracking_notes = tracking_notes[0]
            else:
                picking.write({'tracking_last_up': time.strftime('%Y-%m-%d %H:%M:00')})
                continue

            vals = {
                'tracking_status': tracking_status in tracking_status_map and tracking_status_map[tracking_status] or 'In Transit',
                'tracking_notes': tracking_notes,
                'tracking_last_up': time.strftime('%Y-%m-%d %H:%M:00')
            }
            picking.write(vals)

        log_pool.create({'tracker_id': sobj.id, 'message': "Finishing"})





# TODO move Logs to open in new view when state button is clicked, for better performance