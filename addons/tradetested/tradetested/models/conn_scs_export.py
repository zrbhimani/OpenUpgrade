# -*- coding: utf-8 -*-
from odoo import tools, api, fields, models, _
import time
import datetime
from math import floor
from suds.client import Client

import logging
logging.basicConfig(level=logging.INFO)
_logger = logging.getLogger(__name__)

api_client = False


class scs_export_api(models.Model):
    _name = 'scs.export.api'
    _description = "SCS Export"

    api_key = fields.Char('API KEY', size=64, readonly=True, states={'draft': [('readonly', False)]})
    password = fields.Char('Password', size=64, readonly=True, states={'draft': [('readonly', False)]})
    state = fields.Selection([('draft', 'Draft'), ('connected', 'Connected'), ('error', 'Error')], 'Status',default = 'draft')
    log_ids = fields.One2many('scs.export.log', 'export_api_id', 'Logs')
    debug = fields.Boolean('Debug Mode')

    export_start = fields.Float('Export Start At (GMT/UTC)')
    export_stop = fields.Float('Export Stop At (GMT/UTC)')


    _client = False

    @api.multi
    def set_to_draft(self):
        self.write({'state': 'draft'})

    @api.multi
    def clear_logs(self):
        log_pool = self.env['scs.export.log']
        log_ids = log_pool.search([])
        log_ids.unlink()
        return True

    @api.multi
    def heart_beat(self):
        global api_client
        sobj = self[0]
        if sobj.debug:
            logging.getLogger('suds.client').setLevel(logging.DEBUG)
            logging.getLogger('suds.transport').setLevel(logging.DEBUG)
            logging.getLogger('suds.xsd.schema').setLevel(logging.DEBUG)
            logging.getLogger('suds.wsdl').setLevel(logging.DEBUG)

        if not api_client:
            api_client = Client('https://ecolosys.supplycs.com/ColosysService.svc?wsdl')

        result = api_client.service.HeartBeat()
        if result == 'OK':
            if sobj.state != 'connected':
                self.write({'state': 'connected'})
            return True
        else:
            if sobj.state != 'error':
                self.write({'state': 'error'})
            return False

    @api.model
    def dispatch_request(self, data):

        objs = self.search([])
        assert len(objs) == 1, 'Should have only one SCS Configuration'

        sobj = objs[0]

        if not sobj.heart_beat():
            return False

        print api_client

        obj_dr_header = api_client.factory.create('ns0:ColosysDispatchRequestHeader')

        obj_dr_header.ConsigneeShiptoCode = 'NA'

        if data.get('date'):
            obj_dr_header.DateRequired = data['date']

        if data.get('instructions'):
            obj_dr_header.DeliveryInstructions = data['instructions']

        if data.get('tt_company_name'):
            obj_dr_header.InvoiceName = data['tt_company_name']

        if data.get('phone'):
            obj_dr_header.RepName = data['phone']

        if data.get('priority'):
            obj_dr_header.Priority = data['priority']

        if data.get('ship_street'):
            obj_dr_header.ShiptoStreet = data['ship_street']

        if data.get('ship_city'):
            obj_dr_header.ShiptoCity = data['ship_city']

        if data.get('ship_name'):
            obj_dr_header.ShiptoName = data['ship_name']

        if data.get('ship_zip'):
            obj_dr_header.ShiptoPostcode = data['ship_zip']

        if data.get('ship_suburb'):
            obj_dr_header.ShiptoSuburb = data['ship_suburb']

        if data.get('unique_number'):
            obj_dr_header.UniqueRequestNumber = data['unique_number']

        if data.get('warehouse_note'):
            obj_dr_header.WarehouseNotes = data['warehouse_note']

        for line in data['lines']:
            obj_dr_line = api_client.factory.create('ns0:ColosysDispatchRequestLine')
            obj_dr_line.ClientLineNumber = line['line_number']
            obj_dr_line.ProductCode = line['sku']
            obj_dr_line.QtyOrdered = line['qty']

            obj_dr_header.ColosysDispatchRequestLines.ColosysDispatchRequestLine.append(obj_dr_line)

        obj_dr = api_client.factory.create('ns0:ColosysDispatchRequest')

        obj_dr.ApiKey = sobj.api_key
        obj_dr.Password = sobj.password
        obj_dr.ColosysDispatchRequestHeaders.ColosysDispatchRequestHeader.append(obj_dr_header)

        result = api_client.service.ColosysDispatchRequest(obj_dr)
        res_state = 'error'

        if 'Errors = None' in str(result):
            res_state = 'ok'

        self.env['scs.export.log'].create({'export_api_id': sobj.id, 'picking_id': data['picking_id'], 'response': str(result), 'state': res_state})

        if 'Errors = None' in str(result):
            picking = self.env['stock.picking'].browse(data['picking_id'])
            picking.write({'state': 'processing', 'processed_date': time.strftime('%Y-%m-%d %H:%M:%S')})

        return True

    @api.model
    def export_to_scs_cron(self):
        scs_objs = self.search([])

        if len(scs_objs) == 0:
            return True

        scs = scs_objs[0]
        if scs.export_start and scs.export_stop:
            now = datetime.datetime.now().time()
            start = datetime.time(int(floor(scs.export_start)), int(floor((scs.export_start * 60) % 60)))
            end = datetime.time(int(floor(scs.export_stop)), int(floor((scs.export_stop * 60) % 60)))
            if scs.debug:
                print "Start: %s, Now: %s, Stop: %s" % (start, now, end)
            if not (start <= now <= end):
                return True

        pickings = self.env['stock.picking'].search([('state', '=', 'assigned'), ('picking_type_id.code', 'in', ['internal', 'outgoing'])])

        if pickings:
            _logger.info("Scheduler Export to SCS: %s" % (len(pickings)))

            for picking in pickings:
                try:
                    if picking.picking_type_id.code == 'outgoing':
                        picking.export_to_scs()
                    elif picking.type == 'internal':
                        picking.export_to_scs_internal()
                except Exception, e:
                    _logger.error('%s %s' % (picking.name, e))

        return True


class scs_export_log(models.Model):
    _name = 'scs.export.log'
    _description = 'SCS Export Log'
    _order = 'timestamp desc, id desc'
    _log_access = False,

    export_api_id = fields.Many2one('scs.export.api', 'Export API')
    picking_id = fields.Many2one('stock.picking', 'Delivery Order')
    user_id = fields.Many2one('res.users', 'User',default=lambda self: self._uid)
    response = fields.Text('Response')
    timestamp = fields.Datetime('Timestamp',default=lambda self: time.strftime('%Y-%m-%d %H:%M:%S'))
    state = fields.Selection([('error', 'Error'), ('ok', 'Ok')], string="Status")
