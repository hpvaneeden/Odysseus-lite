#!/usr/bin/env python3
import json
import os
import sys
import tempfile
import shutil

sys.path.insert(0, r'c:\Users\hpvan\Odysseus-lite')

from src.observability import (
    TurnTrace,
    make_model_input_entry,
    make_state_snapshot_entry,
    make_memory_audit_entry,
    make_naming_event_entry,
    make_self_def_event_entry,
)
import src.observability as obs

orig_dir = obs.OBSERVABILITY_DIR
test_dir = tempfile.mkdtemp(prefix='obs_test_')
obs.OBSERVABILITY_DIR = test_dir
obs._ensure_dir()

try:
    with TurnTrace(session_id='test-session-1', turn_id='test-turn-1') as trace:
        trace.model_input = make_model_input_entry(model_input={'message': 'Hello'}, session_id='test-session-1', turn_id='test-turn-1', model='test-model')
        trace.before_state = make_state_snapshot_entry(state={'mood': 'neutral'}, session_id='test-session-1', turn_id='test-turn-1', metadata={'step': 'before'})
        trace.after_state = make_state_snapshot_entry(state={'mood': 'happy'}, session_id='test-session-1', turn_id='test-turn-1', metadata={'step': 'after'})
        trace.record_memory(operation='read', actor='harness', memory_id='mem-123', category='fact', outcome='success')
        trace.record_naming(gate_permitted=True, decision_attempted=True, name_selected='Odysseus', decision_deferred=False, existing_name_preserved=False, user_override=False, persistence_outcome='saved')
        trace.record_self_def(event_type_name='refresh', gate_permitted=True, action_taken=True, outcome='completed')
    for expected_file in ['model_input.jsonl', 'state_snapshot.jsonl', 'memory_audit.jsonl', 'naming_event.jsonl', 'self_def_event.jsonl']:
        fpath = os.path.join(test_dir, expected_file)
        assert os.path.exists(fpath), f'Missing {expected_file}'
        with open(fpath) as f:
            lines = f.readlines()
        assert len(lines) > 0, f'Empty {expected_file}'
        print(f'{expected_file}: OK')
    with TurnTrace(session_id='test-session-2', turn_id='turn-2a') as trace:
        trace.model_input = make_model_input_entry(model_input={'message': 'First call'}, session_id='test-session-2', turn_id='turn-2a')
    with TurnTrace(session_id='test-session-2', turn_id='turn-2b') as trace:
        trace.model_input = make_model_input_entry(model_input={'message': 'Second call'}, session_id='test-session-2', turn_id='turn-2b')
    with open(os.path.join(test_dir, 'model_input.jsonl')) as f:
        all_entries = [json.loads(line) for line in f]
    session_2_entries = [e for e in all_entries if e.get('session_id') == 'test-session-2']
    assert len(session_2_entries) == 2
    print('Multiple model calls: OK')
    with TurnTrace(session_id='test-session-3', turn_id='turn-3') as trace:
        trace.record_memory(operation='search', actor='model', outcome='empty', reason='No matching memories found')
    with open(os.path.join(test_dir, 'memory_audit.jsonl')) as f:
        entries = [json.loads(line) for line in f]
    empty_search = [e for e in entries if e.get('outcome') == 'empty']
    assert len(empty_search) > 0
    print('Empty search results: OK')
    with TurnTrace(session_id='test-session-4', turn_id='turn-4') as trace:
        trace.model_input = make_model_input_entry(model_input={'message': 'Hello', 'api_key': 'sk-secret-123', 'authorization': 'Bearer token-456'}, session_id='test-session-4', turn_id='turn-4')
    with open(os.path.join(test_dir, 'model_input.jsonl')) as f:
        entries = [json.loads(line) for line in f]
    last_entry = entries[-1]
    assert last_entry['model_input']['api_key'] == '[REDACTED]'
    assert last_entry['model_input']['authorization'] == '[REDACTED]'
    print('Sensitive data redaction: OK')
    try:
        with TurnTrace(session_id='test-session-5', turn_id='turn-5') as trace:
            trace.model_input = make_model_input_entry(model_input={'message': 'test'}, session_id='test-session-5', turn_id='turn-5')
            raise ValueError('Simulated failure')
    except ValueError:
        pass
    with open(os.path.join(test_dir, 'model_input.jsonl')) as f:
        entries = [json.loads(line) for line in f]
    failed_entries = [e for e in entries if 'capture_errors' in e]
    assert len(failed_entries) > 0
    print('Failed turn capture errors: OK')

    # ─── Integration tests: observability firing on real dispatch paths ───
    import time as _time

    class _FakeMemory:
        def __init__(self, rows):
            self.rows = list(rows)
            self.incremented = []
        def load(self, owner=None):
            return list(self.rows)
        def increment_uses(self, ids):
            self.incremented.extend(list(ids))

    class _FakeRAG:
        def __init__(self, results):
            self.results = list(results)
        def search(self, query, k=5, owner=None):
            return list(self.results)

    class _FakeDocs:
        def __init__(self, rag_manager):
            self.rag_manager = rag_manager

    class _FakeSession:
        def __init__(self, sid, model, endpoint):
            self.id = sid
            self.model = model
            self.endpoint_url = endpoint
            self.owner = None
            self.name = 'test-session'
            self.mode = 'chat'
            self.rag = False
            self.message_count = 0
            self.headers = {}

    # Work around pre-existing pyotp dependency missing in this env:
    # chat_processor.py imports youtube_handler → services chain → pyotp.
    # Stub those modules so we can import ChatProcessor for integration tests.
    import sys as _sys
    import types as _types

    def _stub(name):
        mod = _types.ModuleType(name)
        _sys.modules[name] = mod
        return mod

    _stub('src.youtube_handler').is_youtube_url = lambda u: False
    _stub('src.chat_helpers').extract_urls = lambda t: []
    _stub('services.youtube.youtube_handler')
    _stub('services.search')
    _sc = _stub('services.search.core')
    _sc.comprehensive_web_search = lambda q, **kw: ('', [])
    _sc.fetch_webpage_content = lambda u: {'success': False, 'error': '', 'content': ''}
    _sc.get_search_config = lambda: {}
    _sc.invalidate_search_cache = lambda: None
    _sc.searxng_search_results = lambda q: []
    _sc.update_search_config = lambda **kw: None
    _sa = _stub('services.search.analytics')
    _sa.get_search_stats = lambda: {}
    _sa.SearchEngineError = Exception
    _sa.NetworkError = Exception
    _sa.ParseError = Exception
    _sa.RateLimitError = Exception
    _stub('services.search.content').fetch_webpage_content = lambda u: {'success': False, 'error': '', 'content': ''}
    _sp = _stub('services.search.providers')
    _sp.searxng_search = lambda q: []
    _sp.searxng_search_api = lambda q: []
    _sp.PROVIDER_INFO = {}

    from src.chat_processor import ChatProcessor

    obs_dir2 = tempfile.mkdtemp(prefix='obs_int_')
    obs.OBSERVABILITY_DIR = obs_dir2
    obs._ensure_dir()
    try:
        mem_rows = [
            {'id': 'm1', 'text': 'User likes dark roast coffee', 'category': 'preference', 'owner': 'alice', 'timestamp': 1},
            {'id': 'm2', 'text': 'User lives in Berlin', 'category': 'fact', 'owner': 'alice', 'timestamp': 2},
        ]
        fake_mem = _FakeMemory(mem_rows)
        fake_rag = _FakeRAG([])
        fake_docs = _FakeDocs(fake_rag)
        session = _FakeSession('s1', 'test-model', 'http://localhost:1234')
        processor = ChatProcessor(memory_manager=fake_mem, personal_docs_manager=fake_docs)

        preface, rag_sources, web_sources = processor.build_context_preface(
            message='Hello there',
            session=session,
            use_memory=True,
            use_rag=False,
            use_web=False,
            owner='alice',
            preferred_name='Alice',
        )

        with open(os.path.join(obs_dir2, 'model_input.jsonl')) as f:
            mi_entries = [json.loads(line) for line in f]
        s1_mi = [e for e in mi_entries if e.get('session_id') == 's1']
        assert len(s1_mi) >= 1, 'No model_input entry for session s1'
        mi = s1_mi[-1]
        assert mi.get('model') == 'test-model'
        assert mi.get('endpoint') == 'http://localhost:1234'
        model_input = mi.get('model_input', {})
        assert 'preface' in model_input
        assert isinstance(model_input['preface'], list)
        assert any(m.get('role') == 'system' for m in model_input['preface'])
        assert model_input.get('preferred_name') == 'Alice'
        assert model_input.get('message') == 'Hello there'
        print('Integration model_input: OK')

        with open(os.path.join(obs_dir2, 'state_snapshot.jsonl')) as f:
            ss_entries = [json.loads(line) for line in f]
        s1_ss = [e for e in ss_entries if e.get('session_id') == 's1']
        before = [e for e in s1_ss if e.get('metadata', {}).get('step') == 'before_processing']
        after = [e for e in s1_ss if e.get('metadata', {}).get('step') == 'after_processing']
        assert len(before) >= 1, 'No before_state snapshot'
        assert len(after) >= 1, 'No after_state snapshot'
        before_state = before[0].get('state', {})
        after_state = after[0].get('state', {})
        assert before_state != {}, 'before_state should not be empty'
        assert after_state != {}, 'after_state should not be empty'
        assert 'session' in after_state
        assert after_state.get('preferred_name') == 'Alice'
        assert after_state.get('memory_count') == 2
        print('Integration state snapshots: OK')

        with open(os.path.join(obs_dir2, 'memory_audit.jsonl')) as f:
            ma_entries = [json.loads(line) for line in f]
        s1_ma = [e for e in ma_entries if e.get('session_id') == 's1']
        load_events = [e for e in s1_ma if e.get('operation') == 'load']
        inc_events = [e for e in s1_ma if e.get('operation') == 'increment_uses']
        assert len(load_events) >= 1, 'No memory load event'
        assert load_events[0].get('actor') == 'harness'
        assert load_events[0].get('outcome') == 'success'
        assert load_events[0].get('after', {}).get('count') == 2
        assert len(inc_events) >= 1, 'No increment_uses event'
        assert inc_events[0].get('actor') == 'harness'
        assert inc_events[0].get('outcome') == 'success'
        print('Integration memory audit: OK')
    finally:
        obs.OBSERVABILITY_DIR = orig_dir
        shutil.rmtree(obs_dir2, ignore_errors=True)

    # --- Test: memory load returns empty (outcome=empty) ---
    obs_dir3 = tempfile.mkdtemp(prefix='obs_empty_')
    obs.OBSERVABILITY_DIR = obs_dir3
    obs._ensure_dir()
    try:
        fake_mem_empty = _FakeMemory([])
        fake_docs_empty = _FakeDocs(_FakeRAG([]))
        session2 = _FakeSession('s2', 'test-model', 'http://localhost:1234')
        processor2 = ChatProcessor(memory_manager=fake_mem_empty, personal_docs_manager=fake_docs_empty)
        processor2.build_context_preface(
            message='Hello',
            session=session2,
            use_memory=True,
            use_rag=False,
            owner='alice',
        )
        with open(os.path.join(obs_dir3, 'memory_audit.jsonl')) as f:
            ma_empty = [json.loads(line) for line in f]
        load_empty = [e for e in ma_empty if e.get('operation') == 'load']
        assert len(load_empty) >= 1, 'No load event for empty memory'
        assert load_empty[0].get('outcome') == 'empty', f'Expected empty outcome, got {load_empty[0].get("outcome")}'
        assert load_empty[0].get('reason') == 'No memories returned for owner'
        print('Integration empty memory load: OK')
    finally:
        obs.OBSERVABILITY_DIR = orig_dir
        shutil.rmtree(obs_dir3, ignore_errors=True)

    # --- Test: RAG search produces memory audit event ---
    obs_dir4 = tempfile.mkdtemp(prefix='obs_rag_')
    obs.OBSERVABILITY_DIR = obs_dir4
    obs._ensure_dir()
    try:
        fake_rag_results = _FakeRAG([
            {'document': 'Doc about Python', 'metadata': {'filename': 'python.txt'}, 'similarity': 0.8},
        ])
        fake_docs_rag = _FakeDocs(fake_rag_results)
        session3 = _FakeSession('s3', 'test-model', 'http://localhost:1234')
        processor3 = ChatProcessor(memory_manager=_FakeMemory([]), personal_docs_manager=fake_docs_rag)
        processor3.build_context_preface(
            message='Python decorators',
            session=session3,
            use_memory=False,
            use_rag=True,
            owner='alice',
        )
        with open(os.path.join(obs_dir4, 'memory_audit.jsonl')) as f:
            ma_rag = [json.loads(line) for line in f]
        search_events = [e for e in ma_rag if e.get('operation') == 'search']
        assert len(search_events) >= 1, 'No RAG search event'
        assert search_events[0].get('actor') == 'harness'
        assert search_events[0].get('outcome') == 'success'
        assert search_events[0].get('after', {}).get('count') == 1
        print('Integration RAG search audit: OK')
    finally:
        obs.OBSERVABILITY_DIR = orig_dir
        shutil.rmtree(obs_dir4, ignore_errors=True)

    # --- Test: naming events for disabled gate and user override ---
    obs_dir5 = tempfile.mkdtemp(prefix='obs_naming_')
    obs.OBSERVABILITY_DIR = obs_dir5
    obs._ensure_dir()
    try:
        import src.self_model as sm

        # refresh() with master switch off -> self_def event gate_permitted=False
        sm.refresh()
        with open(os.path.join(obs_dir5, 'self_def_event.jsonl')) as f:
            sde = [json.loads(line) for line in f]
        gated = [e for e in sde if e.get('outcome') == 'gated_off']
        assert len(gated) >= 1, 'No gated_off self_def event'
        assert gated[0].get('gate_permitted') is False
        assert gated[0].get('action_taken') is False
        print('Integration naming self-def gated_off: OK')

        # save_naming_state with user source -> naming event user_override=True
        model = {
            'schema_version': 1,
            'categories': {'verified': [], 'hypothesis': [], 'aspiration': []},
            'autonomous_naming': {
                'preferred_name': 'TestName',
                'source': 'user',
                'rationale': 'User override',
                'evidence_refs': [],
                'chosen_at': int(_time.time()),
            },
        }
        decision = {
            'preferred_name': 'TestName',
            'source': 'user',
            'rationale': 'User override',
            'evidence_refs': [],
        }
        sm.save_naming_state(model, decision)
        with open(os.path.join(obs_dir5, 'naming_event.jsonl')) as f:
            ne = [json.loads(line) for line in f]
        saved = [e for e in ne if e.get('persistence_outcome') == 'saved']
        assert len(saved) >= 1, 'No saved naming event'
        assert saved[0].get('user_override') is True, f'Expected user_override True, got {saved[0].get("user_override")}'
        assert saved[0].get('name_selected') == 'TestName'
        print('Integration naming save user_override: OK')
    finally:
        obs.OBSERVABILITY_DIR = orig_dir
        shutil.rmtree(obs_dir5, ignore_errors=True)

finally:
    obs.OBSERVABILITY_DIR = orig_dir
    shutil.rmtree(test_dir, ignore_errors=True)
