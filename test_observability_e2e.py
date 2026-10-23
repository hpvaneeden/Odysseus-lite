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
    print('\nAll observability tests PASSED!')
finally:
    obs.OBSERVABILITY_DIR = orig_dir
    shutil.rmtree(test_dir, ignore_errors=True)
