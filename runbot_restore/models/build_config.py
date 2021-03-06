import logging
from odoo import models, fields
from odoo.addons.runbot.common import local_pgadmin_cursor
from odoo.exceptions import UserError
from psycopg2 import sql

_logger = logging.getLogger(__name__)


class Bundle(models.Model):
    _inherit = 'runbot.bundle'

    db_url = fields.Char(help='Path to db backup. Using for restore step', string='Db Path')
    db_name = fields.Char(string='Running database')


class BuildResult(models.Model):
    _inherit = 'runbot.build'

    def _local_pg_copydb(self, dbname, dbcopy):
        self._local_pg_dropdb(dbname)
        _logger.info("createdb %s", dbname)
        with local_pgadmin_cursor() as local_cr:
            local_cr.execute(sql.SQL("""CREATE DATABASE {} TEMPLATE %s LC_COLLATE 'C' ENCODING 'unicode'""").format(sql.Identifier(dbname)), (dbcopy,))
        self.env['runbot.database'].create({'name': dbname, 'build_id': self.id})


class ConfigStep(models.Model):
    _inherit = 'runbot.build.params'

    def create(self, values):
        if values.get('create_batch_id'):
            bundle = self.env['runbot.batch'].browse(values['create_batch_id']).bundle_id
            if bundle and bundle.db_url:
                values['config_data'].update({
                    'dump_url': bundle.db_url,
                })
            if bundle and bundle.db_name:
                values['config_data'].update({
                    'db_name': bundle.db_name,
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
