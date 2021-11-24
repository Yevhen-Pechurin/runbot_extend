# -*- coding: utf-8 -*-
from unittest.mock import patch, mock_open
from odoo.addons.runbot.tests.common import RunbotCase


class TestBuildConfigStep(RunbotCase):

    def setUp(self):
        super(TestBuildConfigStep, self).setUp()

        self.Build = self.env['runbot.build']
        self.ConfigStep = self.env['runbot.build.config.step']
        self.Config = self.env['runbot.build.config']
        self.Database = self.env['runbot.database']

        server_commit = self.Commit.create({
            'name': 'dfdfcfcf0000ffffffffffffffffffffffffffff',
            'repo_id': self.repo_server.id
        })
        self.parent_build = self.Build.create({
            'params_id': self.base_params.copy({'commit_link_ids': [(0, 0, {'commit_id': server_commit.id})]}).id,
        })
        self.start_patcher('find_patcher', 'odoo.addons.runbot.common.find', 0)
        self.start_patcher('local_pg_copydb', 'odoo.addons.runbot_restore.models.build_config.BuildResult._local_pg_copydb', None)
        # self.start_patcher('build_docker_run', 'odoo.addons.runbot.build._docker_run')

    @patch('odoo.addons.runbot.models.build.BuildResult._checkout')
    def test_config_path(self, mock_checkout):
        assert_config_path = '/data/build/config/odoo.conf'

        def docker_run(cmd, log_path, *args, **kwargs):
            config_path = cmd.cmd[cmd.index('-c')+1]
            self.assertEqual(config_path, assert_config_path)

        self.patchers['docker_run'].side_effect = docker_run

        parent_build_params = self.parent_build.params_id.copy({'config_data': {'db_name': 'custom_build'}})
        parent_build = self.parent_build.copy({'params_id': parent_build_params.id})
        config_step = self.ConfigStep.create({
            'name': 'run_test',
            'job_type': 'run_odoo',
            'config_path': '/path/to/config/odoo.conf',
        })
        config_step._run_step(parent_build, 'dev/null/logpath')

    @patch('odoo.addons.runbot.models.build.BuildResult._checkout')
    def test_run_stage(self, mock_checkout):

        def docker_run(cmd, log_path, *args, **kwargs):
            self.assertIn('-u', cmd.cmd)

        self.patchers['docker_run'].side_effect = docker_run

        parent_build_params = self.parent_build.params_id.copy({'config_data': {'db_name': 'custom_build'}})
        parent_build = self.parent_build.copy({'params_id': parent_build_params.id})
        self.Database.create({'name': '%s-%s' % (parent_build.dest, 'stage'), 'build_id': parent_build.id})

        config_step = self.ConfigStep.create({
            'name': 'run_test',
            'job_type': 'stage',
        })
        parent_build.params_id.create_batch_id.bundle_id.db_name = 'new_database'
        config_step._run_step(parent_build, 'dev/null/logpath')
