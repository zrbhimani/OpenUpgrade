# -*- coding: utf-8 -*-
from odoo import tools, api, fields, models, _
from odoo.exceptions import UserError
import os
import tempfile
import base64
import time


class sql_export(models.Model):
    _name = 'sql.export'

    name = fields.Char('Name', size=64, required=True)
    model_id = fields.Many2one('ir.model', 'Model', required=True)
    model = fields.Char('Model', size=64)
    filter_id = fields.Many2one('ir.filters', 'Filter')
    sql = fields.Text('sql', required=True)
    ir_act_window_id = fields.Many2one('ir.actions.act_window')
    ir_value_id = fields.Many2one('ir.values')


    @api.onchange('model_id','filter_id')
    def onchange_model_id(self):
        if self.model_id:
            res = {'model': self.model_id.model}
            res['sql'] = "SELECT * FROM " + self.env[res['model']]._table
            if self.filter_id:
                res['sql'] += ' WHERE id in ({FILTER:%s})' % self.filter_id
            self.update(res)

    @api.multi
    def delete_action(self):
        if self[0].ir_value_id:
            self[0].ir_value_id.unlink()

        if self[0].ir_act_window_id:
            self[0].ir_act_window_id.unlink()

        return True

    @api.multi
    def create_action(self):
        sobj = self[0]

        if sobj.ir_act_window_id or sobj.ir_value_id:
            raise UserError('Menu already created, try delete and recreate')

        ir_act_window = self.env['ir.actions.act_window'].create({
            'name': 'Export %s' % sobj.name,
            'type': 'ir.actions.act_window',
            'res_model': 'sql.export.wizard',
            'src_model': sobj.model_id.model,
            'view_type': 'form',
            'context': "{'sql_export_id' : %d}" % (sobj.id),
            'view_mode': 'form',
            'target': 'new',
            'auto_refresh': 1,
        })

        ir_value = self.env['ir.values'].create({
            'name': 'Export %s' % sobj.name,
            'model': sobj.model_id.model,
            'key2': 'client_action_multi',
            'value': ("ir.actions.act_window," + str(ir_act_window.id)),
            'object': True,
        })
        sobj.update({'ir_act_window_id': ir_act_window.id, 'ir_value_id': ir_value.id,})
        return True


class sql_export_wizard(models.TransientModel):
    _name = 'sql.export.wizard'

    sql_export_id = fields.Many2one('sql.export', 'Exported')
    download_file =  fields.Binary('Download File')
    file_name = fields.Char('Filename', size=64)

    @api.model
    def default_get(self, fields):
        resp = super(sql_export_wizard, self).default_get(fields)
        if 'sql_export_id' in self._context:
            resp['sql_export_id'] = self._context['sql_export_id']
        elif 'active_id' in self._context:
            sql_export_id = self._context['active_id']
            resp['sql_export_id'] = sql_export_id
        return resp

    @api.multi
    def show_download(self):
        path = os.path.join(tempfile.gettempdir(), self.sql_export_id.name + '.csv')
        self._cr.execute("Copy (%s) To '%s' With CSV HEADER;" % (self.sql_export_id.sql, path))
        self.update({
            'file_name': self.sql_export_id.name + "_" + time.strftime('%Y_%m_%d_%H_%M') + '.csv',
            'download_file': open(path, 'rb').read().encode('base64')
        })

        return {
            'name': 'Export: ' + self.sql_export_id.name,
            'res_id': self.id,
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'sql.export.wizard',
            'type': 'ir.actions.act_window',
            'target': 'new',
        }

