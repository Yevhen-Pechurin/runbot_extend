import shlex

from odoo import models, fields
from odoo.addons.runbot.common import now, grep


class ConfigStep(models.Model):
    _inherit = 'runbot.build.config.step'

    job_type = fields.Selection(selection_add=[
        ('restore_database', 'Restore Database'),
        ('stage', 'Stage'),
    ])
    config_path = fields.Char()

    def _run_restore_database(self, build, log_path):
        params = build.params_id
        dump_url = params.config_data.get('dump_url') or build.params_id.create_batch_id.bundle_id.db_url
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

    def _run_stage(self, build, log_path):
        last_ok_builds = build.params_id.create_batch_id.bundle_id.last_batchs.mapped('slot_ids.build_id').filtered(lambda x: x.local_result == 'Ok')
        if last_ok_builds:
            last_build = last_ok_builds[0]
            stage_dbs = last_build.database_ids.filtered(lambda x: 'stage' in x.name)
            if stage_dbs:
                stage_db_name = stage_dbs[0].name
                db_name = '%s-%s' % (build.dest, 'stage')
                build._local_pg_copydb(stage_db_name, db_name)
            else:
                build._log('_run_stage', 'Stage Database not found')
                return
        else:
            build._log('_run_stage', 'Last Build not found')
            if build.params_id.create_batch_id.bundle_id.db_url:
                return self._run_restore_database(build, log_path)
            return

        exports = build._checkout()
        build.job_start = now()

        modules_to_install = self._modules_to_install(build)
        mods = ",".join(modules_to_install)
        python_params = []
        py_version = build._get_py_version()
        cmd = build._cmd(python_params, py_version, sub_command=self.sub_command)
        cmd += ['-d', db_name]
        # list module to update
        extra_params = build.params_id.extra_params or self.extra_params or ''
        if '-u' not in extra_params:
            cmd += ['-u', mods or 'all']

        config_path = build._server("tools/config.py")
        if self.test_enable:
            if grep(config_path, "test-enable"):
                cmd.extend(['--test-enable'])
            else:
                build._log('test_all', 'Installing modules without testing', level='WARNING')
        test_tags_in_extra = '--test-tags' in extra_params
        if self.test_tags or test_tags_in_extra:
            if grep(config_path, "test-tags"):
                if not test_tags_in_extra:
                    test_tags = self.test_tags.replace(' ', '')
                    if self.enable_auto_tags:
                        auto_tags = self.env['runbot.build.error'].disabling_tags()
                        test_tags = ','.join(test_tags.split(',') + auto_tags)
                    cmd.extend(['--test-tags', test_tags])
            else:
                build._log('test_all', 'Test tags given but not supported')
        elif self.enable_auto_tags and self.test_enable:
            if grep(config_path, "[/module][:class]"):
                auto_tags = self.env['runbot.build.error'].disabling_tags()
                if auto_tags:
                    test_tags = ','.join(auto_tags)
                    cmd.extend(['--test-tags', test_tags])

        if grep(config_path, "--screenshots"):
            cmd.add_config_tuple('screenshots', '/data/build/tests')

        if grep(config_path, "--screencasts") and self.env['ir.config_parameter'].sudo().get_param('runbot.enable_screencast', False):
            cmd.add_config_tuple('screencasts', '/data/build/tests')

        cmd.append('--stop-after-init')  # install job should always finish
        if '--log-level' not in extra_params:
            cmd.append('--log-level=test')
        cmd.append('--max-cron-threads=0')

        if extra_params:
            cmd.extend(shlex.split(extra_params))

        cmd.finals.extend(self._post_install_commands(build, modules_to_install, py_version))  # coverage post, extra-checks, ...
        dump_dir = '/data/build/logs/%s/' % db_name
        sql_dest = '%sdump.sql' % dump_dir
        filestore_path = '/data/build/datadir/filestore/%s' % db_name
        filestore_dest = '%s/filestore/' % dump_dir
        zip_path = '/data/build/logs/%s.zip' % db_name
        cmd.finals.append(['pg_dump', '-h', 'host.docker.internal', db_name, '>', sql_dest])
        cmd.finals.append(['cp', '-r', filestore_path, filestore_dest])
        cmd.finals.append(['cd', dump_dir, '&&', 'zip', '-rmq9', zip_path, '*'])
        infos = '{\n    "db_name": "%s",\n    "build_id": %s,\n    "shas": [%s]\n}' % (
            db_name, build.id, ', '.join(['"%s"' % build_commit.commit_id.dname for build_commit in build.params_id.commit_link_ids]))
        build.write_file('logs/%s/info.json' % db_name, infos)

        if self.flamegraph:
            cmd.finals.append(['flamegraph.pl', '--title', 'Flamegraph %s for build %s' % (self.name, build.id), self._perfs_data_path(), '>', self._perfs_data_path(ext='svg')])
            cmd.finals.append(['gzip', '-f', self._perfs_data_path()])  # keep data but gz them to save disc space
        max_timeout = int(self.env['ir.config_parameter'].get_param('runbot.runbot_timeout', default=10000))
        timeout = min(self.cpu_limit, max_timeout)
        env_variables = self.additionnal_env.split(';') if self.additionnal_env else []
        return dict(cmd=cmd, log_path=log_path, build_dir=build._path(), container_name=build._get_docker_name(), cpu_limit=timeout, ro_volumes=exports, env_variables=env_variables)

    def _is_docker_step(self):
        if not self:
            return False
        self.ensure_one()
        return super(ConfigStep, self)._is_docker_step() or self.job_type in ('restore_database', 'stage')

    def _run_run_odoo(self, build, log_path, force=False):
        res = super(ConfigStep, self)._run_run_odoo(build, log_path, force)
        if self.config_path:
            config_name = self.config_path.split('/')[-1]
            res['cmd'] += ['-c', '/data/build/config/%s' % config_name]
            res['ro_volumes'].update({
                'config': self.config_path.replace(self.config_path.split('/')[-1], '')
            })
        return res
