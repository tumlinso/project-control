import io
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import sys
from project_control import admin
from test_admin import _todo_runtime_modules

class ContractSplitAdminTests(unittest.TestCase):
    def test_cli_routes_exact_owner_arguments(self):
        with patch.object(admin,'record_contract_split_integration',return_value={'status':'ready'}) as call, patch('sys.stdout',new_callable=io.StringIO):
            self.assertEqual(admin.main(['record-contract-split-integration','--repo','/repo','--workspace','W',
                '--integration-task','I07','--accepted-commit','abc','--reason','accepted']),0)
        call.assert_called_once_with('/repo','W','I07','abc',reason='accepted',apply=False,confirmation=None)

    def test_apply_requires_exact_confirmation_before_service_open(self):
        with patch.object(admin,'_runtime_identity'):
            with self.assertRaisesRegex(ValueError,'RECORD-CONTRACT-SPLIT-INTEGRATION'):
                admin.record_contract_split_integration('/repo','W','I07','abc',reason='accepted',apply=True)

    def test_forwards_to_transactional_kernel(self):
        service=SimpleNamespace(db=object(),project={'project_uuid':'project'},paths=SimpleNamespace(state_dir=Path('/state')))
        manager=Mock(); manager.return_value.record_contract_split_integration.return_value={'status':'integrated'}
        with patch.object(admin,'_runtime_identity'), patch.dict(sys.modules,_todo_runtime_modules({},service,manager)):
            result=admin.record_contract_split_integration('/repo','W','I07','abc',reason='accepted',apply=True,
                confirmation='RECORD-CONTRACT-SPLIT-INTEGRATION')
        self.assertEqual(result['status'],'integrated')
        manager.return_value.record_contract_split_integration.assert_called_once_with(repository_root=Path('/repo'),workspace_id='W',
            integration_task_id='I07',accepted_commit='abc',reason='accepted',apply=True)
