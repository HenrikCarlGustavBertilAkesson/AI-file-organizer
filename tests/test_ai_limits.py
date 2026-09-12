from pathlib import Path
from types import SimpleNamespace
import importlib.util
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import database
from ai.runtime import Budget, AILimitReached, api_request, usage_scope
from jobs import JobManager, JobCancelled, get_job
from models import File, ProposedAction
from organization_policy import save_policy
from process_pending import process_pending


def response():
    return SimpleNamespace(usage=SimpleNamespace(input_tokens=12, output_tokens=5))


class RuntimeTests(unittest.TestCase):
    def test_retry_backoff_and_usage_are_bounded(self):
        error = RuntimeError('rate limit')
        error.status_code = 429
        call = Mock(side_effect=[error, error, response()])
        budget = Budget(3)
        # Advance a fake clock so no real retry sleeps or API calls occur.
        with patch('ai.runtime.time.monotonic', side_effect=range(100)), \
             patch('ai.runtime.time.sleep'), patch('ai.runtime.random.random', return_value=0):
            budget.request(call)
        self.assertEqual(call.call_count, 3)
        self.assertEqual((budget.usage.attempts, budget.usage.retries), (3, 2))
        self.assertEqual((budget.usage.input_tokens, budget.usage.output_tokens), (12, 5))
        self.assertEqual(budget.usage.unreported_attempts, 2)
        with self.assertRaises(AILimitReached):
            budget.request(call)
        self.assertEqual(call.call_count, 3)

    def test_permanent_errors_are_not_retried(self):
        error = RuntimeError('bad key')
        error.status_code = 401
        call = Mock(side_effect=error)
        with self.assertRaises(RuntimeError):
            Budget(3).request(call)
        self.assertEqual(call.call_count, 1)

    def test_nested_requests_share_allowance(self):
        with usage_scope(1) as budget:
            api_request(response)
            with self.assertRaises(AILimitReached):
                api_request(response)
        self.assertEqual(budget.usage.responses, 1)

    def test_cancel_before_retry_prevents_more_requests(self):
        checkpoint = Mock(side_effect=[None, JobCancelled('stop')])
        error = RuntimeError('rate limited')
        error.status_code = 429
        call = Mock(side_effect=error)
        with self.assertRaises(JobCancelled):
            Budget(3, progress=checkpoint).request(call)
        self.assertEqual(call.call_count, 1)


class BatchAndAgentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / 'files'
        self.root.mkdir()
        override = patch.object(database, 'DATABASE', str(self.root.parent / 'index.db'))
        override.start()
        self.addCleanup(override.stop)
        database.create_database()
        for index in range(6):
            path = self.root / f'{index}.txt'
            path.write_text('contract')
            database.save_file(File(str(path), path.name, '.txt', 8, 0,
                                    content='contract', status='pending'))
        save_policy(self.root, [{'category': 'Work', 'folder': 'Sorted'}], [])
        fake_tools = SimpleNamespace(read_file=Mock(return_value={'content': 'x'*20000}),
                                     classify_path=Mock(),
                                     propose_move=lambda **kwargs: ProposedAction('move', **kwargs))
        spec = importlib.util.spec_from_file_location('bounded_agent_test',
            Path(__file__).resolve().parents[1] / 'app/agents/organizer.py')
        self.agent = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'openai': SimpleNamespace(OpenAI=lambda **kwargs: None),
                                     'tools.file_tools': fake_tools}):
            spec.loader.exec_module(self.agent)

    def test_classification_batch_leaves_remaining_files_pending(self):
        visited = []
        def process(file):
            visited.append(file.path)
            file.status = 'classified'
            database.save_file(file)
            return file
        with patch('process_pending.process_file', side_effect=process):
            first = process_pending(str(self.root), batch_size=2)
            second = process_pending(str(self.root), batch_size=2)
        self.assertEqual((first.classified, first.remaining), (2, 4))
        self.assertEqual((second.classified, second.remaining), (2, 2))
        self.assertEqual(len(set(visited)), 4)
        with self.assertRaises(ValueError):
            process_pending(str(self.root), batch_size=101)

    def test_search_caps_unique_candidates_and_rejects_unseen_reads(self):
        candidates = set()
        result = self.agent.call_tool('search_files', {'query': 'contract', 'page': 1},
                                      str(self.root), candidates=candidates, max_files=2)
        self.assertEqual(len(result['files']), 2)
        self.assertEqual(len(candidates), 2)
        self.assertTrue(result['truncated'])
        with self.assertRaises(ValueError):
            self.agent.call_tool('read_file', {'path': str(self.root / '5.txt')},
                                 str(self.root), candidates=candidates)
        result = self.agent.call_tool('read_file', {'path': sorted(candidates)[0]},
                                     str(self.root), candidates=candidates)
        self.assertLessEqual(len(result['content']), 8000)

    def test_proposal_limit_stops_within_one_model_response(self):
        with database.get_connection() as connection:
            connection.execute("UPDATE files SET category='Work',status='classified'")
        def function(name, arguments, identifier):
            class Call:
                type = 'function_call'
                def model_dump(self):
                    return {'type': self.type, 'name': self.name, 'arguments': self.arguments, 'call_id': self.call_id}
            call = Call()
            call.name, call.arguments, call.call_id = name, __import__('json').dumps(arguments), identifier
            return call
        calls = [function('get_indexed_files', {'page': 1}, 'browse')]
        calls += [function('propose_move', {'source': str(self.root / f'{index}.txt'),
                 'destination': str(self.root / 'Sorted' / f'{index}.txt'), 'reason': 'Organize'}, str(index)) for index in range(3)]
        result = response()
        result.output, result.output_text = calls, ''
        create = Mock(return_value=result)
        self.agent.client = SimpleNamespace(responses=SimpleNamespace(create=create))
        saved = []
        answer = self.agent.run_agent('Organize', str(self.root), max_proposals=1,
                                      proposal_callback=saved.append)
        self.assertEqual(len(answer.proposed_actions), 1)
        self.assertEqual(len(saved), 1)
        self.assertEqual(create.call_count, 1)
        self.assertEqual(create.call_args.kwargs['max_output_tokens'], 4096)
        self.assertEqual(answer.usage['attempts'], 1)

    def test_tool_and_context_caps_stop_further_work(self):
        calls = []
        for index in range(50):
            calls.append(SimpleNamespace(type='function_call', name='get_indexed_files',
                                         arguments='{"page":1}', call_id=str(index)))
        result = response()
        result.output, result.output_text = calls, ''
        create = Mock(return_value=result)
        self.agent.client = SimpleNamespace(responses=SimpleNamespace(create=create))
        with patch.object(self.agent, 'call_tool', return_value={}) as tool:
            answer = self.agent.run_agent('Organize', str(self.root))
        self.assertEqual(tool.call_count, 40)
        self.assertIn('Tool-call limit', answer.message)
        with patch.object(self.agent, 'MAX_CONTEXT_CHARACTERS', 1):
            answer = self.agent.run_agent('Organize', str(self.root))
        self.assertIn('Conversation size', answer.message)
        self.assertEqual(create.call_count, 1)

    def test_usage_is_saved_even_when_job_fails(self):
        def runner(*args, **kwargs):
            api_request(response)
            raise ValueError('later failure')
        manager = JobManager(runner)
        self.addCleanup(manager.close)
        job = manager.submit('organize', {'root': str(self.root)})
        manager.pool.shutdown(wait=True)
        stored = get_job(job['id'])
        self.assertEqual(stored['status'], 'failed')
        self.assertEqual(stored['usage']['input_tokens'], 12)
        self.assertEqual(stored['usage']['attempts'], 1)


if __name__ == '__main__':
    unittest.main()
