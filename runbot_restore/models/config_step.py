import os
import shlex

from odoo import models, fields
from odoo.addons.runbot.common import now, grep
from odoo.addons.runbot.container import docker_get_gateway_ip


class ConfigStep(models.Model):
    _inherit = 'runbot.build.config.step'

    job_type = fields.Selection(selection_add=[
        ('restore_database', 'Restore Database'),
        ('run_odoo_with_database', 'Run odoo with database'),
    ])
    config_path = fields.Char()

    def _run_restore_database(self, build, log_path):
        params = build.params_id
        dump_url = params.config_data.get('dump_url') or build.params_id.create_batch_id.bundle_id.dump_url
        assert dump_url
        zip_name = dump_url.split('/')[-1]
        build._log('Restoring', 'Restoring db [%s](%s)' % (zip_name, dump_url), log_type='markdown')
        restore_suffix = self.restore_rename_db_suffix or params.dump_db.db_suffix or self.name or 'restore'
        assert restore_suffix
        restore_db_name = build.params_id.create_batch_id.bundle_id.db_name or params.config_data.get('db_name') or '%s-%s' % (build.dest, restore_suffix)

        build._local_pg_createdb(restore_db_name)
        cmd = ' && '.join([
            'mkdir /data/build/restore',
            'cd /data/build/restore',
            'cp /data/build/source/%s .' % zip_name,
            'unzip -q %s' % zip_name,
            'echo "### restoring filestore"',
            'mkdir -p /data/build/datadir/filestore/%s' % restore_db_name,
            'mv filestore/* /data/build/datadir/filestore/%s' % restore_db_name,
            'echo "###restoring db"',
            'psql -q %s < dump.sql' % (restore_db_name),
            'cd /data/build',
            'echo "### cleaning"',
            'rm -r restore',
            'echo "### listing modules"',
            """psql %s -c "select name from ir_module_module where state = 'installed'" -t -A > /data/build/logs/restore_modules_installed.txt""" % restore_db_name,

        ])
        ro_volumes = {
            'source': dump_url.replace(dump_url.split('/')[-1], ''),
        }
        return dict(cmd=cmd, log_path=log_path, build_dir=build._path(), container_name=build._get_docker_name(), cpu_limit=self.cpu_limit, ro_volumes=ro_volumes)

    def _run_run_odoo_with_database(self, build, log_path):
        exports = build._checkout()
        build.job_start = now()

        # adjust job_end to record an accurate job_20 job_time
        build._log('run', 'Start running build %s' % build.dest)
        # run server
        cmd = build._cmd(local_only=False)
        if os.path.exists(build._get_server_commit()._source_path('addons/im_livechat')):
            cmd += ["--workers", "2"]
            cmd += ["--longpolling-port", "8070"]
            cmd += ["--max-cron-threads", "1"]
        else:
            # not sure, to avoid old server to check other dbs
            cmd += ["--max-cron-threads", "0"]

        db_name = build.params_id.create_batch_id.bundle_id.db_name
        cmd += ['-d', db_name]

        icp = self.env['ir.config_parameter'].sudo()
        nginx = icp.get_param('runbot.runbot_nginx', True)
        if grep(build._server("tools/config.py"), "proxy-mode") and nginx:
            cmd += ["--proxy-mode"]

        if grep(build._server("tools/config.py"), "db-filter"):
            if nginx:
                cmd += ['--db-filter', '%d.*$']
            else:
                cmd += ['--db-filter', '%s.*$' % build.dest]
        smtp_host = docker_get_gateway_ip()
        if smtp_host:
            cmd += ['--smtp', smtp_host]

        extra_params = self.extra_params or ''
        if extra_params:
            cmd.extend(shlex.split(extra_params))
        env_variables = self.additionnal_env.split(';') if self.additionnal_env else []

        docker_name = build._get_docker_name()
        build_path = build._path()
        build_port = build.port
        self.env.cr.commit()  # commit before docker run to be 100% sure that db state is consistent with dockers
        self.invalidate_cache()
        build.params_id.create_batch_id.bundle_id.last_batch.slot_ids.build_id._ask_kill()
        self.env['runbot.runbot']._reload_nginx()
        return dict(cmd=cmd, log_path=log_path, build_dir=build_path, container_name=docker_name, exposed_ports=[build_port, build_port + 1], ro_volumes=exports, env_variables=env_variables)

    def _is_docker_step(self):
        if not self:
            return False
        self.ensure_one()
        return super(ConfigStep, self)._is_docker_step() or self.job_type in ('restore_database', 'run_odoo_with_database')

    def _run_run_odoo(self, build, log_path, force=False):
        res = super(ConfigStep, self)._run_run_odoo(build, log_path, force)
        if self.config_path:
            config_name = self.config_path.split('/')[-1]
            res['cmd'] += '-c /data/build/config/%s' % config_name
            res['ro_volumes'].update({
                'config': self.config_path.replace(self.config_path.split('/')[-1], '')
            })
        return res
