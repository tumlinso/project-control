import unittest
from tests.todo.v2_helpers import base_plan, safe_task
from todo_orchestrator.plan import validate_plan
from todo_orchestrator.models import TodoError

class PlanExecutionContractsTests(unittest.TestCase):
    def plan(self, role):
        plan=base_plan([safe_task('A','src/a')]); plan['schema_version']=3
        plan['interfaces']=[{'id':'API','owner_task_id':'A','contract_paths':['src/a/api.hh']}]
        plan['runs']=[{'id':'RUN','root_task_id':'A','charter':{},'lanes':[
            {'id':'LANE','role':role,'tasks':['A']}]}]
        return plan

    def test_integrator_interface_owner_is_valid(self):
        self.assertTrue(validate_plan(self.plan('integrator'))['valid'])

    def test_validator_cannot_own_publication_responsibility(self):
        with self.assertRaises(TodoError) as error:
            validate_plan(self.plan('validator'))
        self.assertTrue(any('cannot publish' in item for item in error.exception.details['errors']))

    def test_cuda_gate_cannot_bypass_controller_resources(self):
        plan=self.plan('integrator');plan['tasks'][0]['gates']=[{
            'id':'GPU','type':'command','argv':['true'],'cuda':{'gpus':0}}]
        with self.assertRaises(TodoError):
            validate_plan(plan)
        plan['tasks'][0]['gates'][0]['cuda']={'gpus':1}
        self.assertTrue(validate_plan(plan)['valid'])
