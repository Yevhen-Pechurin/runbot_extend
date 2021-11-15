from odoo import models, fields
from odoo.exceptions import UserError


class Bundle(models.Model):
    _inherit = 'runbot.bundle'

    db_url = fields.Char(help='Using for restore step')


class ConfigStep(models.Model):
    _inherit = 'runbot.build.params'

    def create(self, values):
        if values.get('create_batch_id'):
            bundle = self.env['runbot.batch'].browse(values['create_batch_id']).bundle_id
            if bundle and bundle.db_url:
                values['config_data'].update({
                    'dump_url': bundle.db_url,
                    'db_name': 'restore',
                })
        return super(ConfigStep, self).create(values)


class Config(models.Model):
    _inherit = 'runbot.build.config'

    def _check_step_ids_order(self):
        install_job = False
        step_ids = self.step_ids()
        for step in step_ids:
            if step.job_type in ('install_odoo', 'restore'):
                install_job = True
            if step.job_type == 'run_odoo':
                if step != step_ids[-1]:
                    raise UserError('Jobs of type run_odoo should be the last one')
                if not install_job:
                    raise UserError('Jobs of type run_odoo should be preceded by a job of type install_odoo')
        self._check_recustion()
