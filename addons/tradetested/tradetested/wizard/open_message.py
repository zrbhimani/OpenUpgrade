# -*- coding: utf-8 -*-
from odoo import tools, api, fields, models, _


class open_message(models.Model):
    _name = 'open.message'

    message_ids = fields.Many2many('mail.message', 'so_message_rel', 'wizard_id', 'message_id', 'Messages')

    @api.model
    def default_get(self, fields):
        res = {}
        so_obj = self.env[self._context.get('active_model')].browse(self._context.get('active_id'))
        msg_ids = []
        for message in so_obj.message_ids:
            if message.message_type != 'notification':
                msg_ids.append(message.id)
        res['message_ids'] = msg_ids
        return res

    @api.multi
    def button_save(self):
        return True

class Invite(models.TransientModel):
    _inherit = 'mail.wizard.invite'
    _description = 'Invite wizard'

    @api.multi
    def add_followers(self):
        email_from = self.env['mail.message']._get_default_from()
        for wizard in self:
            Model = self.env[wizard.res_model]
            document = Model.browse(wizard.res_id)

            # filter partner_ids to get the new followers, to avoid sending email to already following partners
            new_partners = wizard.partner_ids - document.message_partner_ids
            new_channels = wizard.channel_ids - document.message_channel_ids
            document.message_subscribe(new_partners.ids, new_channels.ids)

            #removed code section for sending email when subscriber added using gui
        return {'type': 'ir.actions.act_window_close'}
