# -*- coding: utf-8 -*-
import psycopg2
from odoo import fields, models, api

class TradetestedConfigSettings(models.TransientModel):
    _name = 'tradetested.config.settings'

    section_name = fields.Char(string='Section Name', stored=True)
    values = fields.Jsonb(string='Values')

class ThumborConfig(models.AbstractModel):
    _name = 'tradetested.config.thumbor'

    thumbor_key = fields.Char(string='Thumbor Key', help="""Key for generating thumbnails for product images etc.""", manual=True)
    domain = fields.Char(string='Domain', help="""Domain that images are served from""", manual=True)

    @api.model
    def _setup_base(self, partial):
        super(ThumborConfig, self)._setup_base(partial)
        settings = self.env['tradetested.config.settings']
        settings._setup_base(False)
        settings._setup_fields(False)
        self._set_default_from_settings()

    def _set_default_from_settings(self):
        try:
            instance = self.env['tradetested.config.settings'].search([('section_name', '=', 'thumbor')])
            if instance.values:
                for key, val in instance.values.iteritems():
                    f = self._fields[key]
                    f.default = fields.default_old_to_new(f, val)
        except psycopg2.ProgrammingError:
            self.env.cr.rollback()
            pass

    @api.model
    def _create(self, vals):
        instance = self.env['tradetested.config.settings'].search([('section_name', '=', 'thumbor')])
        vals = {'section_name': 'thumbor', 'values': {'thumbor_key': vals['thumbor_key'], 'domain': vals['domain']}}
        if instance:
            instance.write(vals)
        else:
            instance.create(vals)
        self._set_default_from_settings()


class TradetestedConfig(models.AbstractModel):
    _name = 'tradetested.config'

    thumbor = ThumborConfig

    @api.model
    def _setup_base(self, partial):
        super(TradetestedConfig, self)._setup_base(partial)
        for model_name in ['thumbor']:
            model = self.env['tradetested.config.'+model_name]
            model._setup_base(False)
            setattr(self, model_name, model)
            for name, field in model._fields.items():
                if (name not in('display_name', 'id')):
                    self._add_field(model_name+':'+name, field)

    @api.model
    def _create(self, vals):
        collected_vals = {}
        for name, sub_vals in vals.iteritems():
            model_name, key = name.split(':')
            collected_vals[model_name] = collected_vals.get(model_name) or {}
            collected_vals[model_name][key] = sub_vals
        for model_name, model_vals in collected_vals.iteritems():
            self.env['tradetested.config.'+model_name].create(model_vals)

    @api.model
    def get(self, key):
        method = self._fields[key].default
        if method:
            return method(self)



